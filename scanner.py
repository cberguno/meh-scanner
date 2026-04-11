"""
Full scan pipeline entry point for the dashboard.
Runs: search → enrich → analyze → filter → export → return results.
Uses the project's existing StructuredLogger (not structlog).
"""
import json
import re
import time

from affiliate import apply_affiliate_url
from alerts import check_and_fire_alerts
from analyzer import analyze_sites_batch
from sheets import append_deals
from config import Config
from dashboard_export import export_daily_dashboard, write_project_root_candidate_files
from scoring import build_candidate_records
from db import record_source_visit
from logger import logger
from scraper import (
    enrich_candidates,
    get_last_search_diagnostics,
    mark_candidates_seen,
    search_for_deal_sites,
)


def run_full_scan(force_domains: frozenset = frozenset()) -> dict:
    """
    Execute a complete meh-scanner scan synchronously.
    Designed to be called via asyncio.to_thread() from the dashboard.

    Returns:
        {
            "success":      bool,
            "deals":        list[dict] | None,   # full deal objects for DB archiving
            "deals_count":  int,
            "candidates":   int,
            "runtime":      float,
            "error":        str | None,
        }
    """
    start = time.time()
    logger.info("scan_started", "Full scan starting…")

    # Config check
    if not Config.SERPER_API_KEY or Config.SERPER_API_KEY == "your_serper_key_here":
        msg = "SERPER_API_KEY not configured"
        logger.error("config_error", msg)
        return _fail(msg, start)

    # ── counters for scan_summary ────────────────────────────────────────────
    _discovered = 0
    _enriched   = 0
    _analyzed   = 0
    _parse_fail = 0
    _scored     = 0
    _filtered   = 0
    _sheets_status = "skip"
    _marked_seen = 0
    search_diag: dict = {}

    # ── Phase 1: search & enrich ────────────────────────────────────────────
    try:
        logger.info("search_phase", "Searching for deal sites…")
        sites = search_for_deal_sites(force_domains=force_domains)
        search_diag = get_last_search_diagnostics()
        if not sites:
            logger.warning("no_sites_found", "No sites returned by search")
            return _ok([], 0, start, search_diag=search_diag)

        _discovered = len(sites)
        logger.info("sites_found", f"Found {len(sites)} candidates", count=len(sites))
        sites = enrich_candidates(sites)
        _enriched = len(sites)
        logger.info("debug_enriched", f"DEBUG enriched: {len(sites)} sites going to analysis", count=len(sites))
    except Exception as exc:
        return _fail(f"Search/enrich failed: {exc}", start)

    # ── Phase 2: analyze ────────────────────────────────────────────────────
    try:
        logger.info("analysis_phase", f"Analyzing {len(sites)} sites…")
        analyses = analyze_sites_batch(sites)
        _analyzed = len(analyses)
        _marked_seen = mark_candidates_seen([item["site"] for item in analyses])
        logger.info("debug_analyses", f"DEBUG analysis done: {len(analyses)} results", count=len(analyses))
        for i, _item in enumerate(analyses[:3]):
            logger.info("debug_sample", f"DEBUG sample {i+1}: site={_item['site'].get('title','?')!r} raw={_item['analysis'][:300]!r}", index=i+1, site=_item['site'].get('title',''), raw=_item['analysis'][:300])
    except Exception as exc:
        return _fail(f"Analysis failed: {exc}", start)

    # ── Phase 3: filter & build deal list ───────────────────────────────────
    all_candidates = build_candidate_records(analyses)
    _parse_fail = sum(
        1 for c in all_candidates if c.get("rejection_reason") == "analysis parse error"
    )
    _scored = sum(1 for c in all_candidates if c.get("quality_score") is not None)
    deals: list[dict] = []
    for c in all_candidates:
        if not c.get("accepted"):
            continue
        _filtered += 1
        deals.append(
            apply_affiliate_url(
                _compute_roi(
                    {
                        "site_name": c["site_name"],
                        "url": c["url"],
                        "rationale": c.get("rationale") or "",
                        "niche": c.get("niche", ""),
                        "quality_score": c["quality_score"],
                        "deal_price": c.get("deal_price", ""),
                        "original_price": c.get("original_price", ""),
                    }
                )
            )
        )

    for item, c in zip(analyses, all_candidates):
        logger.info(
            "debug_score",
            f"DEBUG score: {c.get('site_name', '?')!r} vibe={c.get('vibe_score')} → q={c.get('quality_score')}",
            title=c.get("site_name", ""),
            vibe_score=c.get("vibe_score"),
            quality_score=c.get("quality_score"),
        )
        if c.get("rejection_reason") == "analysis parse error":
            logger.warning(
                "analysis_parse_failed",
                f"Could not parse analysis for {item['site'].get('title')}",
                site=item["site"].get("title"),
                error=item["analysis"][:500],
            )

    logger.info("debug_filtered", f"DEBUG filter result: {len(deals)} deals passed score >= 6", count=len(deals))

    # ── Phase 3e: write candidates.csv / candidates.txt and print to console ─
    try:
        write_project_root_candidate_files(all_candidates)

        print(f"\n=== SCAN CANDIDATES ({len(all_candidates)} total) ===")
        if not all_candidates:
            print("NO CANDIDATES FOUND")
        else:
            print(
                "site_name | vibe_score | deal_title | quality_score | accepted | reason"
            )
            for _c in all_candidates:
                print(
                    " | ".join(
                        [
                            str(_c.get("site_name") or ""),
                            str(_c.get("vibe_score") if _c.get("vibe_score") is not None else ""),
                            str(_c.get("deal_title") or "")[:40],
                            str(_c.get("quality_score") if _c.get("quality_score") is not None else ""),
                            "yes" if _c.get("accepted") else "no",
                            str(_c.get("rejection_reason") or ""),
                        ]
                    )
                )
        print("=" * 60)
    except Exception as _exc:
        print(f"[candidates dump failed: {_exc}]")

    # ── Phase 3b: fire alerts for high-quality deals ────────────────────────
    check_and_fire_alerts(deals)

    # ── Phase 3c: record per-source quality metrics ──────────────────────────
    deal_urls = {d["url"] for d in deals}
    for item in analyses:
        url = item["site"].get("link", "")
        if not url:
            continue
        try:
            score = json.loads(item["analysis"]).get("quality_score", 0)
        except Exception:
            score = 0
        record_source_visit(url, deal_found=url in deal_urls, deal_score=float(score))

    # ── Phase 3d: write to Google Sheet ─────────────────────────────────────
    try:
        _sheets_status = "ok" if append_deals(deals) else "skip"
    except Exception as exc:
        _sheets_status = "fail"
        logger.error("sheets_unexpected", f"Unexpected error writing to sheet: {exc}", error=str(exc))

    # ── Phase 4: export dashboard ────────────────────────────────────────────
    runtime = time.time() - start
    try:
        export_daily_dashboard(deals, candidates_count=len(sites), runtime_seconds=runtime,
                               all_candidates=all_candidates)
    except Exception as exc:
        logger.error("dashboard_export_failed", f"Export failed (continuing): {exc}", error=str(exc))

    _missing = [k for k, v in [
        ("SERPER_API_KEY",    Config.SERPER_API_KEY),
        ("ANTHROPIC_API_KEY", Config.ANTHROPIC_API_KEY),
        ("GOOGLE_SHEET_ID",   Config.GOOGLE_SHEET_ID),
        ("GOOGLE_SERVICE_ACCOUNT_JSON", Config.GOOGLE_SERVICE_ACCOUNT_JSON),
    ] if not v]
    logger.info(
        "scan_summary",
        f"scan_summary discovered={_discovered} enriched={_enriched} analysis={_analyzed} "
        f"parse_fail={_parse_fail} scored={_scored} filtered={_filtered} deals={len(deals)} "
        f"sheets={_sheets_status} search_ok={search_diag.get('queries_succeeded', 0)}/{search_diag.get('queries_total', 0)} "
        f"live={search_diag.get('selected_search_candidates', 0)} seed={search_diag.get('selected_seed_candidates', 0)} "
        f"seen_marked={_marked_seen} missing_keys={_missing}",
        discovered=_discovered, enriched=_enriched, analysis=_analyzed,
        parse_fail=_parse_fail, scored=_scored, filtered=_filtered,
        deals=len(deals), sheets=_sheets_status, missing_keys=_missing,
        search_queries_total=search_diag.get("queries_total", 0),
        search_queries_succeeded=search_diag.get("queries_succeeded", 0),
        search_queries_failed=search_diag.get("queries_failed", 0),
        selected_search_candidates=search_diag.get("selected_search_candidates", 0),
        selected_seed_candidates=search_diag.get("selected_seed_candidates", 0),
        degraded_search=search_diag.get("degraded_search", False),
        low_yield_search=search_diag.get("low_yield_search", False),
        relaxed_vibe_threshold_used=search_diag.get("relaxed_vibe_threshold_used", False),
        search_drop_reasons=search_diag.get("drop_reasons", {}),
        seen_marked=_marked_seen,
    )

    logger.info(
        "scan_complete",
        f"Scan done: {len(deals)} deals from {len(sites)} candidates in {runtime:.1f}s",
        deals_count=len(deals),
        candidates=len(sites),
        runtime=round(runtime, 1),
    )

    return {
        "success":     True,
        "deals":       deals,
        "deals_count": len(deals),
        "candidates":  len(sites),
        "runtime":     round(runtime, 1),
        "error":       None,
        "all_candidates": all_candidates,
        "summary": {
            "discovered":  _discovered,
            "enriched":    _enriched,
            "analyzed":    _analyzed,
            "parse_fail":  _parse_fail,
            "scored":      _scored,
            "filtered":    _filtered,
            "deals":       len(deals),
            "sheets":      _sheets_status,
            "missing_keys": _missing,
            "search_queries_total": search_diag.get("queries_total", 0),
            "search_queries_succeeded": search_diag.get("queries_succeeded", 0),
            "search_queries_failed": search_diag.get("queries_failed", 0),
            "search_live": search_diag.get("selected_search_candidates", 0),
            "search_seed": search_diag.get("selected_seed_candidates", 0),
            "search_degraded": search_diag.get("degraded_search", False),
            "search_low_yield": search_diag.get("low_yield_search", False),
            "search_relaxed_threshold": search_diag.get("relaxed_vibe_threshold_used", False),
            "search_drop_summary": _format_drop_summary(search_diag.get("drop_reasons", {})),
            "seen_marked": _marked_seen,
        },
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _fail(msg: str, start: float) -> dict:
    logger.error("scan_failed", msg, error=msg)
    return {
        "success":     False,
        "deals":       None,
        "deals_count": 0,
        "candidates":  0,
        "runtime":     round(time.time() - start, 1),
        "error":       msg,
    }


def _parse_price(s: str) -> float | None:
    """
    Parse a price string such as '$29.99', '£149', '29.99' → float.
    Returns None if no numeric value can be extracted.
    """
    if not s:
        return None
    m = re.search(r"[\d]+(?:[.,]\d+)*", s.replace(",", ""))
    if not m:
        return None
    try:
        val = float(m.group().replace(",", ""))
        return val if val > 0 else None
    except ValueError:
        return None


def _compute_roi(deal: dict) -> dict:
    """
    Attach estimated_value, profit, and roi_pct to a deal dict.

    Method
    ──────
    If original_price is available AND greater than deal_price, use it as the
    resale value proxy (retail price ≈ what someone would pay elsewhere).
    Otherwise assume a conservative 30% markup on the deal price.

    All three fields are None when deal_price cannot be parsed.
    """
    price  = _parse_price(deal.get("deal_price", ""))
    retail = _parse_price(deal.get("original_price", ""))

    if price is None or price <= 0:
        return {**deal, "estimated_value": None, "profit": None, "roi_pct": None}

    if retail and retail > price:
        est = retail
    else:
        est = round(price * 1.3, 2)

    profit  = round(est - price, 2)
    roi_pct = round(profit / price * 100, 1)

    return {**deal, "estimated_value": est, "profit": profit, "roi_pct": roi_pct}


def _ok(deals: list, candidates: int, start: float, *, search_diag: dict | None = None) -> dict:
    search_diag = search_diag or {}
    return {
        "success":     True,
        "deals":       deals,
        "deals_count": len(deals),
        "candidates":  candidates,
        "runtime":     round(time.time() - start, 1),
        "error":       None,
        "summary": {
            "discovered": candidates,
            "enriched": 0,
            "analyzed": 0,
            "parse_fail": 0,
            "scored": 0,
            "filtered": 0,
            "deals": len(deals),
            "sheets": "skip",
            "missing_keys": [],
            "search_queries_total": search_diag.get("queries_total", 0),
            "search_queries_succeeded": search_diag.get("queries_succeeded", 0),
            "search_queries_failed": search_diag.get("queries_failed", 0),
            "search_live": search_diag.get("selected_search_candidates", 0),
            "search_seed": search_diag.get("selected_seed_candidates", 0),
            "search_degraded": search_diag.get("degraded_search", False),
            "search_low_yield": search_diag.get("low_yield_search", False),
            "search_relaxed_threshold": search_diag.get("relaxed_vibe_threshold_used", False),
            "search_drop_summary": _format_drop_summary(search_diag.get("drop_reasons", {})),
            "seen_marked": 0,
        },
    }


def _format_drop_summary(drop_reasons: dict) -> str:
    if not drop_reasons:
        return ""
    parts = []
    for reason, count in sorted(drop_reasons.items(), key=lambda item: (-item[1], item[0]))[:4]:
        if count:
            parts.append(f"{reason}={count}")
    return ", ".join(parts)
