"""
FastAPI dashboard for meh-scanner.

Architecture
────────────
* asyncio.to_thread()        — scan runs in a worker thread; event loop stays free
* lifespan context           — init DB + load deals on startup;
                               close all SSE queues on shutdown
* DashboardState             — all reads/writes under threading.Lock so the
                               sync worker thread and async routes never race
* sse-starlette              — EventSourceResponse manages the SSE wire protocol
* Broadcaster (sse.py)       — fan-out asyncio.Queue; one queue per browser tab
* Three SSE events:
    scan-started   — fired when scan begins (timestamp)
    scan-complete  — fired when scan finishes (metrics + error if any)
    status-update  — fired immediately on connect so new/reconnected tabs sync
* Jinja2Templates            — FastAPI-integrated, autoescape on
* No structlog               — uses project StructuredLogger from logger.py
                               Signature: logger.info(event, message, **ctx)
"""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import html as html_mod
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from config import Config
from db import init_db, record_scan, archive_deals, get_recent_scans, get_source_stats, get_source_status
from logger import logger
from scanner import run_full_scan
from sse import (
    broadcaster,
    KEEPALIVE,
    EVENT_SCAN_STARTED,
    EVENT_SCAN_COMPLETE,
    EVENT_STATUS_UPDATE,
)


# ── templates ─────────────────────────────────────────────────────────────────

templates = Jinja2Templates(directory="templates")


# ── shared state ──────────────────────────────────────────────────────────────

class DashboardState:
    """
    Thread-safe container for all dashboard runtime data.

    The async FastAPI routes and the sync worker thread that runs the scan both
    access this object.  Every public method acquires self._lock.  Callers
    receive copies of data (not references) so they can't mutate state outside
    the lock.

    Note: _completion_pending has been removed.  SSE broadcasts replace the
    old one-shot-flag + HX-Trigger polling mechanism.
    """

    def __init__(self) -> None:
        self._lock               = threading.Lock()
        self.deals:               list[dict]     = []
        self.scan_in_progress:    bool            = False
        self.last_scan_time:      Optional[str]   = None
        self.last_error:          Optional[str]   = None

    # ── reads ─────────────────────────────────────────────────────────────────

    def get_deals(self) -> list[dict]:
        """Return a shallow copy of the deals list; safe to iterate outside lock."""
        with self._lock:
            return list(self.deals)

    def get_snapshot(self) -> dict:
        """Consistent point-in-time snapshot of all scalar state fields."""
        with self._lock:
            return {
                "scan_in_progress": self.scan_in_progress,
                "deals_count":      len(self.deals),
                "last_scan_time":   self.last_scan_time,
                "last_error":       self.last_error,
            }

    def get_metrics(self) -> dict:
        """Snapshot extended with computed metrics for cards + SSE payloads."""
        with self._lock:
            scores = [d.get("quality_score", 0) for d in self.deals]
            return {
                "scan_in_progress": self.scan_in_progress,
                "deals_count":      len(self.deals),
                "last_scan_time":   self.last_scan_time,
                "last_error":       self.last_error,
                # human-friendly aliases used by metrics.html template
                "total_deals":      len(self.deals),
                "avg_score":        round(sum(scores) / len(scores), 1) if scores else 0,
                "best_score":       max(scores) if scores else 0,
                "last_scan":        self.last_scan_time or "Never",
            }

    # ── writes ────────────────────────────────────────────────────────────────

    def set_scan_started(self) -> None:
        with self._lock:
            self.scan_in_progress = True
            self.last_error       = None

    def set_scan_finished(self, *, error: Optional[str] = None) -> None:
        with self._lock:
            self.scan_in_progress = False
            self.last_error       = error

    def load_deals_from_file(self) -> None:
        """
        Reload deals from public/latest.json.
        Falls back to the most recent successful scan in SQLite if the file
        is missing (e.g. after a deploy that wipes public/).
        Called on startup and after each successful scan.
        """
        path = Path("public") / "latest.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                with self._lock:
                    self.deals          = data.get("deals", [])
                    self.last_scan_time = data.get("generated_at")
                return
        except Exception as exc:
            logger.error(
                "load_deals_failed",
                f"Could not reload public/latest.json: {exc}",
                error=str(exc),
            )

        # Fallback: pull the most recent deals from SQLite so the dashboard
        # is not empty when latest.json is absent.
        try:
            from db import get_deal_history
            rows = get_deal_history(days=1)
            if rows:
                with self._lock:
                    self.deals = rows
                    # last_scan_time stays None — will be set on next scan
                logger.info(
                    "load_deals_fallback",
                    f"Loaded {len(rows)} deals from SQLite (latest.json absent)",
                    count=len(rows),
                )
        except Exception as exc:
            logger.error(
                "load_deals_fallback_failed",
                f"SQLite fallback also failed: {exc}",
                error=str(exc),
            )


state = DashboardState()


# ── background scan ───────────────────────────────────────────────────────────

async def _run_scan_background(force_domains: frozenset = frozenset()) -> None:
    """
    Fire-and-forget coroutine that:

      1. Updates state + broadcasts scan-started (event loop)
      2. Runs the full synchronous scan in a worker thread (asyncio.to_thread)
      3. Archives results to SQLite
      4. Broadcasts status-update (metrics refresh) after new deals load
      5. Always broadcasts scan-complete in the finally block — even if an
         exception is raised inside record_scan or archive_deals

    Broadcasting always happens back on the event loop (after await) so we
    never touch asyncio.Queue from a background thread.
    """
    state.set_scan_started()

    await broadcaster.publish(EVENT_SCAN_STARTED, {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "scan_in_progress": True,
        "message":          "Scan started",
    })

    # Track outcome for the finally block — set by whichever branch runs.
    _success:      bool          = False
    _deals_count:  int           = 0
    _error:        Optional[str] = None

    try:
        # ── run the blocking scan pipeline in a worker thread ─────────────────
        result: dict = await asyncio.to_thread(run_full_scan, force_domains)

        if result["success"]:
            state.load_deals_from_file()
            deals = result.get("deals") or []

            # Persist to database
            scan_id = record_scan(
                success=True,
                deals_count=result["deals_count"],
                candidates=result.get("candidates", 0),
                runtime_seconds=result["runtime"],
            )
            if deals:
                archive_deals(scan_id, deals)

            state.set_scan_finished(error=None)
            _success     = True
            _deals_count = result["deals_count"]

            # Push updated metrics immediately so connected tabs refresh their
            # cards without waiting for the deals-table hx-get to complete.
            await broadcaster.publish(EVENT_STATUS_UPDATE, state.get_metrics())

        else:
            _error = result.get("error") or "Unknown error"
            record_scan(
                success=False,
                deals_count=0,
                candidates=result.get("candidates", 0),
                runtime_seconds=result.get("runtime", 0),
                error=_error,
            )
            state.set_scan_finished(error=_error)

    except Exception as exc:
        _error = str(exc)
        logger.error(
            "scan_exception",
            f"Unhandled exception in scan background task: {_error}",
            error=_error,
        )
        try:
            record_scan(success=False, deals_count=0, runtime_seconds=0, error=_error)
        except Exception:
            pass
        state.set_scan_finished(error=_error)

    finally:
        # Guaranteed to fire regardless of success, known failure, or exception.
        # The finally block runs after state.set_scan_finished(), so
        # scan_in_progress is already False when metrics are read.
        snap = state.get_snapshot()
        payload: dict = {
            "success":          _success,
            "deals_count":      _deals_count,
            "last_scan_time":   snap["last_scan_time"],
            "scan_in_progress": False,
            "error":            _error,
        }
        if _success:
            # Spread full metrics into the payload so the JS toast can show
            # accurate numbers (total_deals, avg_score, best_score).
            payload.update(state.get_metrics())
        await broadcaster.publish(EVENT_SCAN_COMPLETE, payload)


# ── lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ───────────────────────────────────────────────────────────────
    logger.info("dashboard_startup", "Dashboard starting…")
    init_db()

    _missing = [k for k, v in [
        ("SERPER_API_KEY",    Config.SERPER_API_KEY),
        ("ANTHROPIC_API_KEY", Config.ANTHROPIC_API_KEY),
    ] if not v]
    if _missing:
        logger.warning(
            "missing_api_keys",
            f"Missing required API keys: {', '.join(_missing)} — scans will not produce results",
            missing_keys=_missing,
        )

    state.load_deals_from_file()
    snap = state.get_snapshot()
    logger.info(
        "dashboard_ready",
        f"Dashboard ready — {snap['deals_count']} deals loaded",
        deals_count=snap["deals_count"],
    )
    yield

    # ── shutdown ──────────────────────────────────────────────────────────────
    logger.info("dashboard_shutdown", "Dashboard shutting down…")
    await broadcaster.close_all()
    logger.info("dashboard_shutdown_complete", "All SSE connections closed")


# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Meh-Scanner Dashboard", lifespan=lifespan)


# ── page route ────────────────────────────────────────────────────────────────

def _missing_keys() -> list[str]:
    return [k for k, v in [
        ("SERPER_API_KEY",             Config.SERPER_API_KEY),
        ("ANTHROPIC_API_KEY",          Config.ANTHROPIC_API_KEY),
        ("GOOGLE_SHEET_ID",            Config.GOOGLE_SHEET_ID),
        ("GOOGLE_SERVICE_ACCOUNT_JSON", Config.GOOGLE_SERVICE_ACCOUNT_JSON),
    ] if not v]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"snapshot": state.get_snapshot(), "missing_keys": _missing_keys()},
    )


@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    """Public-facing MVP landing page."""
    return templates.TemplateResponse(request=request, name="landing.html", context={})


# ── SSE endpoint ──────────────────────────────────────────────────────────────

@app.get("/api/events")
async def sse_events(request: Request):
    """
    Long-lived SSE stream managed by sse-starlette's EventSourceResponse.
    One connection per browser tab (managed by htmx-ext-sse on the client).

    Lifecycle
    ─────────
    connect   → subscribe to broadcaster queue
              → immediately yield status-update so reconnecting tabs sync
    idle      → yield {"comment": "keepalive"} every 15 s
    event     → yield message dict from broadcaster queue
    shutdown  → broadcaster puts None sentinel → generator breaks
    disconnect→ detected via request.is_disconnected(); queue removed
    """
    q = await broadcaster.subscribe()
    logger.info(
        "sse_connected",
        f"SSE client connected ({broadcaster.client_count} total)",
        clients=broadcaster.client_count,
    )

    async def generator() -> AsyncGenerator[dict, None]:
        # ── Initial sync: send current state immediately on (re)connect ───────
        # This ensures a tab that reloads mid-scan sees the correct banner / badge.
        yield {"event": EVENT_STATUS_UPDATE, "data": json.dumps(state.get_metrics())}

        # ── Main event loop ───────────────────────────────────────────────────
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Nothing from the broadcaster — send a keepalive comment
                    yield KEEPALIVE
                    continue

                if msg is None:
                    # Sentinel from broadcaster.close_all() during app shutdown
                    break

                yield msg

                # Check for client disconnect after each message delivery.
                # Starlette sets this flag when the TCP connection is gone.
                if await request.is_disconnected():
                    break

        finally:
            await broadcaster.unsubscribe(q)
            logger.info(
                "sse_disconnected",
                f"SSE client disconnected ({broadcaster.client_count} remaining)",
                clients=broadcaster.client_count,
            )

    return EventSourceResponse(generator())


# ── HTMX partial routes ───────────────────────────────────────────────────────

@app.get("/api/status_html", response_class=HTMLResponse)
async def status_html():
    """
    Status badge HTML fragment.
    Fetched via hx-trigger="load, sse:scan-started, sse:scan-complete" so the
    badge stays accurate both on page load and on every SSE state change.
    No HX-Trigger header — SSE is now the event bus.
    """
    snap        = state.get_snapshot()
    in_progress = snap["scan_in_progress"]
    error       = snap["last_error"]

    if in_progress:
        return HTMLResponse(
            '<span class="badge badge-info gap-1">'
            '<span class="loading loading-ring loading-xs"></span>'
            'Scanning…</span>'
        )
    if error:
        short = html_mod.escape(error[:45])
        full  = html_mod.escape(error)
        return HTMLResponse(
            f'<span class="badge badge-error" title="{full}">❌ {short}</span>'
        )
    return HTMLResponse('<span class="badge badge-success">✓ Ready</span>')


@app.get("/api/deals", response_class=HTMLResponse)
async def deals_partial(
    request:      Request,
    search_query: str  = "",
    min_score:    int  = 6,
    niche_filter: str  = "",
    hq_only:      bool = False,
):
    """
    Filtered deals table fragment.
    Called on:
      • initial page load             (hx-trigger="load")
      • sse:scan-complete SSE event   (hx-trigger="sse:scan-complete")
      • filter / search form change   (normal HTMX form request)
    hx-include="#filter-form" preserves the user's current filter state on
    SSE-triggered refreshes.
    """
    all_deals = state.get_deals()
    filtered  = _filter_deals(
        all_deals,
        search_query=search_query,
        min_score=min_score,
        niche_filter=niche_filter,
        hq_only=hq_only,
    )
    return templates.TemplateResponse(
        request=request,
        name="deals_table.html",
        context={"deals": filtered, "total": len(all_deals), "filtered": len(filtered)},
    )


@app.get("/api/metrics_html", response_class=HTMLResponse)
async def metrics_html(request: Request):
    """
    Metrics cards fragment.
    Called on load and on sse:scan-complete.
    """
    m = state.get_metrics()
    return templates.TemplateResponse(
        request=request,
        name="metrics.html",
        context={
            "total_deals": m["total_deals"],
            "avg_score":   m["avg_score"],
            "best_score":  m["best_score"],
            "last_scan":   m["last_scan"],
        },
    )


@app.get("/api/sources_html", response_class=HTMLResponse)
async def sources_html(request: Request):
    """
    Source quality table fragment.
    Called on load and on sse:scan-complete so the table refreshes after each scan.
    """
    return templates.TemplateResponse(
        request=request,
        name="sources_table.html",
        context={"sources": get_source_stats()},
    )


# ── action routes ─────────────────────────────────────────────────────────────

@app.post("/api/scan")
async def trigger_scan(request: Request):
    """
    Trigger a background scan.  Returns 409 if one is already running.
    Progress and completion are communicated via SSE — not via this response.

    Optional JSON body:
      {"force_domains": ["example.com", "woot.com"]}
    Domains listed here bypass the 'remove' status filter for this scan only.
    Omitting the body (e.g. the dashboard button) is fine — defaults to no overrides.
    """
    if state.get_snapshot()["scan_in_progress"]:
        raise HTTPException(status_code=409, detail="A scan is already running")

    force_domains: frozenset = frozenset()
    try:
        body = await request.json()
        force_domains = frozenset(str(d).lower().strip() for d in body.get("force_domains", []))
    except Exception:
        pass  # empty body or non-JSON (normal dashboard button click)

    if force_domains:
        logger.info("scan_triggered_forced", "Manual scan triggered with force_domains",
                    force_domains=sorted(force_domains))
    else:
        logger.info("scan_triggered", "Manual scan triggered from dashboard")

    asyncio.create_task(_run_scan_background(force_domains))
    return {"started": True, "force_domains": sorted(force_domains)}


# ── data / debug routes ───────────────────────────────────────────────────────

@app.get("/api/status")
async def status_json():
    """JSON status snapshot — useful for debugging and external integrations."""
    return state.get_snapshot()


@app.get("/api/metrics")
async def metrics_json():
    """JSON metrics snapshot."""
    return state.get_metrics()


@app.get("/api/sources")
async def sources():
    """Per-source quality metrics — scans seen, hit rate, retention score, status."""
    return {"sources": get_source_stats()}


@app.get("/api/history")
async def history():
    """Recent scan runs from the database."""
    return {"scans": get_recent_scans(limit=20)}


@app.post("/api/track_click")
async def track_click(request: Request):
    """
    Fire-and-forget affiliate click tracker.
    Called via navigator.sendBeacon() from the deals table — no response body needed.
    Logs a structured 'affiliate_click' entry; extend here to write to DB or analytics.
    """
    try:
        body = await request.json()
        url = body.get("url", "")
    except Exception:
        url = ""
    logger.info(
        "affiliate_click",
        f"Affiliate link clicked: {url}",
        url=url,
    )
    return {"ok": True}


@app.post("/api/track_cta")
async def track_cta(request: Request):
    """Landing page CTA click tracker. Called via navigator.sendBeacon()."""
    logger.info("cta_click", "Landing page CTA clicked — Get Early Access")
    return {"ok": True}


@app.get("/health")
async def health():
    """Health check — includes live SSE client count."""
    return {
        "status":      "ok",
        "sse_clients": broadcaster.client_count,
    }


# ── filter helper ─────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    """Extract bare hostname from a URL (no www. prefix)."""
    from urllib.parse import urlparse
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _filter_deals(
    deals: list[dict],
    *,
    search_query: str,
    min_score: int,
    niche_filter: str,
    hq_only: bool = False,
) -> list[dict]:
    result = deals

    if hq_only:
        result = [
            d for d in result
            if (d.get("quality_score") or 0) >= 8
            and d.get("original_price")
            and get_source_status(_domain(d.get("url", ""))) in ("keep", "new")
        ]
    elif min_score:
        result = [d for d in result if (d.get("quality_score") or 0) >= min_score]

    if search_query:
        q = search_query.lower()
        result = [
            d for d in result
            if q in (d.get("site_name")  or "").lower()
            or q in (d.get("rationale")  or "").lower()
            or q in (d.get("niche")      or "").lower()
        ]

    if niche_filter:
        result = [d for d in result if d.get("niche") == niche_filter]

    result.sort(key=lambda d: d.get("quality_score") or 0, reverse=True)
    return result


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
