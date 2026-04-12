"""
Google Sheets integration for meh-scanner.

Authentication: service account JSON stored in GOOGLE_SERVICE_ACCOUNT_JSON env var.
Accepts either a raw JSON string or a base64-encoded JSON string.

The service account must be shared as an Editor on the target spreadsheet.
Tab name: "Deals"  (create manually if it doesn't exist)
"""
import base64
import json
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import Config
from logger import logger

SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_TAB = "Deals"
HISTORY_TAB = "History"
HEADERS   = ["Site", "URL", "Niche", "Score", "Price", "Was", "Est. ROI %", "Rationale", "Scanned At"]
HISTORY_HEADERS = ["ID", "Site", "URL", "Niche", "Score", "Price", "Was", "Rationale", "Archived At", "Scan Run At"]


def _load_credentials():
    """
    Parse GOOGLE_SERVICE_ACCOUNT_JSON and return service_account.Credentials.
    Returns None if the variable is missing or malformed.
    """
    raw = Config.GOOGLE_SERVICE_ACCOUNT_JSON.strip()
    if not raw:
        return None

    # Try raw JSON first; fall back to base64-encoded JSON
    info = None
    try:
        info = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception as exc:
            logger.error(
                "sheets_creds_invalid",
                f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON or base64: {exc}",
                error=str(exc),
            )
            return None

    try:
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except Exception as exc:
        logger.error(
            "sheets_creds_build_failed",
            f"Failed to build service account credentials: {exc}",
            error=str(exc),
        )
        return None


def append_deals(deals: list[dict]) -> bool:
    """
    Append new deals to the Google Sheet.

    - Skips duplicates by checking existing URLs in column B.
    - Writes column headers automatically on first use (empty sheet).
    - Returns True on success, False on any failure.
    - Never raises — safe to call without a surrounding try/except.
    """
    if not Config.GOOGLE_SHEET_ID:
        logger.warning("sheets_skip", "GOOGLE_SHEET_ID not set — skipping sheet write")
        return False

    creds = _load_credentials()
    if creds is None:
        logger.warning("sheets_skip", "GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid — skipping sheet write")
        return False

    if not deals:
        logger.info("sheets_empty", "No deals to write to Google Sheet")
        return True

    try:
        svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheet = svc.spreadsheets().values()

        # ── Fetch existing rows to detect duplicates ──────────────────────────
        try:
            existing = sheet.get(
                spreadsheetId=Config.GOOGLE_SHEET_ID,
                range=f"{SHEET_TAB}!A:B",
            ).execute()
        except Exception as exc:
            logger.warning(
                "sheets_fetch_warn",
                f"Could not read existing sheet rows (will append anyway): {exc}",
                error=str(exc),
            )
            existing = {}

        rows_so_far   = existing.get("values", [])
        has_header    = bool(rows_so_far)
        existing_urls = {r[1] for r in rows_so_far[1:] if len(r) > 1}  # skip header row

        # ── Deduplicate ───────────────────────────────────────────────────────
        new_deals = [d for d in deals if d.get("url", "") not in existing_urls]
        if not new_deals:
            logger.info("sheets_no_new", "All deals already present in sheet — nothing written")
            return True

        # ── Build payload ─────────────────────────────────────────────────────
        now     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        payload: list[list] = []

        if not has_header:
            payload.append(HEADERS)

        for d in new_deals:
            roi = f"{d['roi_pct']}%" if d.get("roi_pct") is not None else ""
            payload.append([
                d.get("site_name",      ""),
                d.get("url",            ""),
                d.get("niche",          ""),
                d.get("quality_score",  ""),
                d.get("deal_price",     ""),
                d.get("original_price", ""),
                roi,
                d.get("rationale",      ""),
                now,
            ])

        # ── Append to sheet ───────────────────────────────────────────────────
        sheet.append(
            spreadsheetId=Config.GOOGLE_SHEET_ID,
            range=f"{SHEET_TAB}!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": payload},
        ).execute()

        logger.info(
            "sheets_write_ok",
            f"Wrote {len(new_deals)} deal(s) to Google Sheet",
            count=len(new_deals),
            sheet_id=Config.GOOGLE_SHEET_ID,
        )
        return True

    except Exception as exc:
        logger.error(
            "sheets_write_failed",
            f"Google Sheet write failed: {exc}",
            error=str(exc),
        )
        return False


def _ensure_tab(svc, sheet_id: str, tab_name: str) -> None:
    """
    Create *tab_name* in the spreadsheet if it does not already exist.
    Silently ignores errors (e.g. tab already exists).
    """
    try:
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        if tab_name not in existing:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
            logger.info("sheets_tab_created", f"Created tab '{tab_name}' in spreadsheet", tab=tab_name)
    except Exception as exc:
        logger.warning("sheets_tab_create_warn", f"Could not ensure tab '{tab_name}': {exc}", error=str(exc))


def publish_all_deals(deals: list[dict]) -> bool:
    """
    Write the complete historical deal archive to the ``History`` tab of the
    configured Google Sheet.  The tab is created automatically if absent.

    Unlike :func:`append_deals` (which appends only *new* deals per scan),
    this function **replaces** the entire History tab on every call so the
    export is always a faithful, self-contained snapshot of the SQLite data.

    Args:
        deals: rows returned by :func:`db.get_all_deals`.

    Returns:
        True on success, False on any failure.
    """
    if not Config.GOOGLE_SHEET_ID:
        logger.warning("sheets_history_skip", "GOOGLE_SHEET_ID not set — skipping history export")
        return False

    creds = _load_credentials()
    if creds is None:
        logger.warning(
            "sheets_history_skip",
            "GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid — skipping history export",
        )
        return False

    if not deals:
        logger.info("sheets_history_empty", "No historical deals to export")
        return True

    try:
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

        # Ensure the History tab exists
        _ensure_tab(svc, Config.GOOGLE_SHEET_ID, HISTORY_TAB)

        # Build payload: header row + one row per deal
        payload: list[list] = [HISTORY_HEADERS]
        for d in deals:
            payload.append([
                d.get("id",             ""),
                d.get("site_name",      ""),
                d.get("url",            ""),
                d.get("niche",          ""),
                d.get("quality_score",  ""),
                d.get("deal_price",     ""),
                d.get("original_price", ""),
                d.get("rationale",      ""),
                d.get("archived_at",    ""),
                d.get("scan_timestamp", ""),
            ])

        sheet = svc.spreadsheets().values()

        # Clear the tab first so we always get a clean, up-to-date snapshot
        last_col = chr(ord("A") + len(HISTORY_HEADERS) - 1)
        data_range = f"{HISTORY_TAB}!A:{last_col}"
        sheet.clear(
            spreadsheetId=Config.GOOGLE_SHEET_ID,
            range=data_range,
        ).execute()

        # Write the full dataset
        sheet.update(
            spreadsheetId=Config.GOOGLE_SHEET_ID,
            range=f"{HISTORY_TAB}!A1",
            valueInputOption="RAW",
            body={"values": payload},
        ).execute()

        logger.info(
            "sheets_history_ok",
            f"Published {len(deals)} historical deal(s) to '{HISTORY_TAB}' tab",
            count=len(deals),
            sheet_id=Config.GOOGLE_SHEET_ID,
        )
        return True

    except Exception as exc:
        logger.error(
            "sheets_history_failed",
            f"History export to Google Sheet failed: {exc}",
            error=str(exc),
        )
        return False
