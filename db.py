"""
SQLite persistence layer for the meh-scanner dashboard.
WAL mode enabled for better read concurrency.
All writes are serialised with a module-level threading.Lock.
"""
import json as _json
import sqlite3
import threading
import re
from pathlib import Path
from urllib.parse import urlparse

from logger import logger

DB_PATH = Path("data") / "meh_scanner.db"
_LOCK = threading.Lock()


# ── connection factory ────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    """Open a connection with WAL mode and FK enforcement."""
    c = sqlite3.connect(str(DB_PATH), timeout=10.0, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.row_factory = sqlite3.Row
    return c


# ── schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and indexes if they don't already exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        con = _conn()
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    success         INTEGER NOT NULL,
                    deals_count     INTEGER NOT NULL DEFAULT 0,
                    candidates      INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds REAL,
                    error           TEXT
                );

                CREATE TABLE IF NOT EXISTS deals (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_run_id     INTEGER NOT NULL REFERENCES scan_runs(id),
                    site_name       TEXT,
                    url             TEXT    NOT NULL,
                    rationale       TEXT,
                    niche           TEXT,
                    quality_score   REAL,
                    deal_price      TEXT,
                    original_price  TEXT,
                    archived_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(url)
                );

                CREATE INDEX IF NOT EXISTS idx_scan_runs_ts   ON scan_runs(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_deals_scan     ON deals(scan_run_id);
                CREATE INDEX IF NOT EXISTS idx_deals_archived ON deals(archived_at DESC);

                CREATE TABLE IF NOT EXISTS source_stats (
                    domain          TEXT PRIMARY KEY,
                    first_seen      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    scans_seen      INTEGER NOT NULL DEFAULT 0,
                    deals_found     INTEGER NOT NULL DEFAULT 0,
                    score_sum       REAL    NOT NULL DEFAULT 0.0,
                    recent_outcomes TEXT    NOT NULL DEFAULT '[]',
                    status          TEXT    NOT NULL DEFAULT 'new'
                );

                CREATE INDEX IF NOT EXISTS idx_source_last ON source_stats(last_seen DESC);
                
                CREATE TABLE IF NOT EXISTS seen_sites (
                    url TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_seen_sites_last ON seen_sites(last_seen DESC);
            """)
            con.commit()
            # Migration: add trusted column if this is an existing DB without it
            try:
                con.execute("ALTER TABLE source_stats ADD COLUMN trusted INTEGER NOT NULL DEFAULT 0")
                # Backfill trusted flag for qualifying existing rows
                con.execute("UPDATE source_stats SET trusted = 1 WHERE status = 'keep' AND scans_seen >= 5")
                con.commit()
            except Exception:
                pass  # Column already exists
            logger.info("db_ready", "Database initialised", path=str(DB_PATH))
            # Cleanup seen_sites entries older than 1 day so daily scans can revisit candidates.
            try:
                cur = con.execute("DELETE FROM seen_sites WHERE last_seen < datetime('now','-1 day')")
                deleted = cur.rowcount
                if deleted and deleted > 0:
                    logger.info("db_cleanup", deleted=deleted, message=f"Cleaned up {deleted} old entries from seen_sites")
                con.commit()
            except Exception:
                # Non-fatal cleanup failure
                pass
            # Migrate any existing seen_sites rows into source_stats to consolidate tracking.
            try:
                rows = con.execute("SELECT url, first_seen, last_seen FROM seen_sites").fetchall()
                for r in rows:
                    url = r["url"]
                    domain = _extract_domain(url)
                    if not domain:
                        continue
                    # Insert a minimal source_stats row if domain not already tracked.
                    con.execute(
                        """INSERT OR IGNORE INTO source_stats
                           (domain, first_seen, last_seen, scans_seen, deals_found, score_sum, recent_outcomes, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            domain,
                            r.get("first_seen") or None,
                            r.get("last_seen") or None,
                            1,
                            0,
                            0.0,
                            "[]",
                            "new",
                        ),
                    )
                con.commit()
            except Exception:
                # Non-fatal migration failure; keep running
                pass
        finally:
            con.close()


# ── write helpers ─────────────────────────────────────────────────────────────

def record_scan(
    *,
    success: bool,
    deals_count: int,
    candidates: int = 0,
    runtime_seconds: float = 0.0,
    error: str | None = None,
) -> int:
    """Insert a scan_run row and return its id."""
    with _LOCK:
        con = _conn()
        try:
            cur = con.execute(
                """INSERT INTO scan_runs (success, deals_count, candidates, runtime_seconds, error)
                   VALUES (?, ?, ?, ?, ?)""",
                (int(success), deals_count, candidates, runtime_seconds, error),
            )
            con.commit()
            scan_id = cur.lastrowid
            logger.info(
                "scan_recorded",
                f"Scan {scan_id} recorded (success={success}, deals={deals_count})",
                scan_id=scan_id,
                success=success,
                deals_count=deals_count,
            )
            return scan_id
        finally:
            con.close()


def archive_deals(scan_run_id: int, deals: list[dict]) -> int:
    """
    Archive deals for a scan run.
    Duplicates (same URL) are silently ignored.
    Returns the number of rows actually inserted.
    """
    if not deals:
        return 0

    inserted = 0
    with _LOCK:
        con = _conn()
        try:
            for d in deals:
                try:
                    con.execute(
                        """INSERT OR IGNORE INTO deals
                           (scan_run_id, site_name, url, rationale, niche,
                            quality_score, deal_price, original_price)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scan_run_id,
                            d.get("site_name"),
                            d.get("url"),
                            d.get("rationale"),
                            d.get("niche"),
                            d.get("quality_score"),
                            d.get("deal_price"),
                            d.get("original_price"),
                        ),
                    )
                    inserted += con.execute("SELECT changes()").fetchone()[0]
                except sqlite3.Error as e:
                    logger.warning("archive_deal_skip", f"Skipped deal: {e}", url=d.get("url"), error=str(e))
            con.commit()
            logger.info("deals_archived", f"Archived {inserted}/{len(deals)} deals for scan {scan_run_id}", scan_run_id=scan_run_id, inserted=inserted)
        finally:
            con.close()
    return inserted


# ── read helpers ──────────────────────────────────────────────────────────────

def get_recent_scans(limit: int = 20) -> list[dict]:
    """Return the most recent scan runs as a list of dicts."""
    with _LOCK:
        con = _conn()
        try:
            rows = con.execute(
                """SELECT id, timestamp, success, deals_count, candidates, runtime_seconds, error
                   FROM scan_runs ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()


def get_deal_history(days: int = 7) -> list[dict]:
    """Return deals archived in the last N days."""
    with _LOCK:
        con = _conn()
        try:
            rows = con.execute(
                """SELECT site_name, url, rationale, niche, quality_score,
                          deal_price, original_price, archived_at
                   FROM deals
                   WHERE archived_at >= datetime('now', ? || ' days')
                   ORDER BY archived_at DESC""",
                (f"-{days}",),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()


# ── source quality scoring ────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Return the bare hostname (no www.) for a URL."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or url


def _compute_source_status(scans_seen: int, deals_found: int, recent: list[int]) -> str:
    """
    Derive a retention status from cumulative and recent scan outcomes.

    Formula
    ───────
    all_time_hit_rate  = deals_found / scans_seen
    recent_hit_rate    = mean of the last ≤10 outcomes  (1 = deal found, 0 = miss)
    retention_score    = 0.6 × recent_hit_rate + 0.4 × all_time_hit_rate

    Thresholds (require ≥3 scans before acting):
      new        — fewer than 3 scans seen
      keep       — retention_score ≥ 0.30
      quarantine — retention_score ≥ 0.10  (and < 0.30)
      remove     — retention_score < 0.10  AND scans_seen ≥ 5
    """
    if scans_seen < 3:
        return "new"
    all_time = deals_found / scans_seen
    recent_rate = sum(recent) / len(recent) if recent else 0.0
    retention = 0.6 * recent_rate + 0.4 * all_time
    if retention >= 0.30:
        return "keep"
    if retention >= 0.10:
        return "quarantine"
    if scans_seen >= 5:
        return "remove"
    return "quarantine"


def record_source_visit(url: str, *, deal_found: bool, deal_score: float = 0.0) -> None:
    """
    Record one scan visit for the source domain of *url*.
    Called once per analyzed candidate after each scan run.
    Thread-safe; uses the module-level _LOCK.
    """
    domain = _extract_domain(url)
    if not domain:
        return

    with _LOCK:
        con = _conn()
        try:
            row = con.execute(
                "SELECT scans_seen, deals_found, score_sum, recent_outcomes "
                "FROM source_stats WHERE domain = ?",
                (domain,),
            ).fetchone()

            if row:
                scans_seen  = row["scans_seen"] + 1
                deals_found_count = row["deals_found"] + (1 if deal_found else 0)
                score_sum   = row["score_sum"] + (deal_score if deal_found else 0.0)
                recent      = _json.loads(row["recent_outcomes"] or "[]")
            else:
                scans_seen        = 1
                deals_found_count = 1 if deal_found else 0
                score_sum         = deal_score if deal_found else 0.0
                recent            = []

            recent.append(1 if deal_found else 0)
            recent = recent[-10:]          # keep only the last 10 outcomes
            status = _compute_source_status(scans_seen, deals_found_count, recent)
            avg_score = round(score_sum / deals_found_count, 2) if deals_found_count else 0.0
            trusted = 1 if (status == "keep" and scans_seen >= 5) else 0

            con.execute(
                """INSERT INTO source_stats
                       (domain, last_seen, scans_seen, deals_found, score_sum,
                        recent_outcomes, status, trusted)
                   VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       last_seen       = CURRENT_TIMESTAMP,
                       scans_seen      = excluded.scans_seen,
                       deals_found     = excluded.deals_found,
                       score_sum       = excluded.score_sum,
                       recent_outcomes = excluded.recent_outcomes,
                       status          = excluded.status,
                       trusted         = excluded.trusted""",
                (domain, scans_seen, deals_found_count, score_sum,
                 _json.dumps(recent), status, trusted),
            )
            con.commit()
            logger.info(
                "source_visit_recorded",
                f"Source {domain}: scans={scans_seen} deals={deals_found_count} "
                f"avg_score={avg_score} status={status}",
                domain=domain, scans_seen=scans_seen,
                deals_found=deals_found_count, avg_score=avg_score, status=status,
            )
        finally:
            con.close()


def get_source_status(domain: str) -> str:
    """Return the current status for a domain, or 'new' if not yet tracked."""
    with _LOCK:
        con = _conn()
        try:
            row = con.execute(
                "SELECT status FROM source_stats WHERE domain = ?", (domain,)
            ).fetchone()
            return row["status"] if row else "new"
        finally:
            con.close()


def get_trusted_domains() -> set[str]:
    """Return the set of domains currently marked trusted (status=keep, scans_seen>=5)."""
    with _LOCK:
        con = _conn()
        try:
            rows = con.execute(
                "SELECT domain FROM source_stats WHERE trusted = 1"
            ).fetchall()
            return {r["domain"] for r in rows}
        finally:
            con.close()


def get_source_stats(limit: int = 100) -> list[dict]:
    """
    Return per-source quality metrics ordered by last_seen desc.
    Each row includes a computed avg_score and retention_score for display.
    """
    with _LOCK:
        con = _conn()
        try:
            rows = con.execute(
                """SELECT domain, first_seen, last_seen, scans_seen,
                          deals_found, score_sum, recent_outcomes, status
                   FROM source_stats
                   ORDER BY last_seen DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                recent = _json.loads(r["recent_outcomes"] or "[]")
                scans  = r["scans_seen"]
                found  = r["deals_found"]
                avg_score     = round(r["score_sum"] / found, 2) if found else 0.0
                all_time_rate = round(found / scans, 3) if scans else 0.0
                recent_rate   = round(sum(recent) / len(recent), 3) if recent else 0.0
                retention     = round(0.6 * recent_rate + 0.4 * all_time_rate, 3)
                result.append({
                    **dict(r),
                    "avg_deal_score":    avg_score,
                    "all_time_hit_rate": all_time_rate,
                    "recent_hit_rate":   recent_rate,
                    "retention_score":   retention,
                })
            return result
        finally:
            con.close()


# ── seen_sites helpers ─────────────────────────────────────────────────────
def _normalize_url_for_seen(url: str) -> str:
    if not url:
        return ""
    u = url.lower().strip()
    u = re.sub(r'[\?#].*$', '', u)
    u = re.sub(r'/+$', '', u)
    return u


def is_site_seen(url: str) -> bool:
    """Return True if the normalized URL already exists in seen_sites."""
    norm = _normalize_url_for_seen(url)
    if not norm:
        return False
    with _LOCK:
        con = _conn()
        try:
            row = con.execute("SELECT 1 FROM seen_sites WHERE url = ?", (norm,)).fetchone()
            return row is not None
        finally:
            con.close()


def mark_site_seen(url: str) -> None:
    """Insert or update a seen_sites row for the normalized URL."""
    norm = _normalize_url_for_seen(url)
    if not norm:
        return
    with _LOCK:
        con = _conn()
        try:
            con.execute(
                """INSERT INTO seen_sites (url, first_seen, last_seen)
                   VALUES (?, COALESCE((SELECT first_seen FROM seen_sites WHERE url = ?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
                   ON CONFLICT(url) DO UPDATE SET last_seen = CURRENT_TIMESTAMP""",
                (norm, norm),
            )
            con.commit()
        finally:
            con.close()
