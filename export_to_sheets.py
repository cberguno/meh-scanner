#!/usr/bin/env python3
"""
Export all vetted products from the local SQLite database to Google Sheets.

Vetted products are deals that passed the quality-score threshold (≥ 6) in
any previous scan run and were archived to the local database.

Usage
-----
Export everything:
    python export_to_sheets.py

Export the 100 most recent deals:
    python export_to_sheets.py --limit 100

Prerequisites
-------------
Set the following environment variables (or add them to a .env file):

    GOOGLE_SHEET_ID               – the spreadsheet ID from its URL
    GOOGLE_SERVICE_ACCOUNT_JSON   – service-account JSON (raw or base64)

See README.md for full Google Sheets API setup instructions.
"""

import argparse
import sqlite3

from db import DB_PATH, init_db
from logger import logger
from sheets import append_deals


def get_all_archived_deals(limit: int | None = None) -> list[dict]:
    """
    Read vetted deals from the local SQLite database.

    Returns rows ordered newest-first.  When *limit* is given, only the
    most recent *limit* rows are returned.
    """
    if not DB_PATH.exists():
        logger.warning(
            "export_no_db",
            f"Database not found at {DB_PATH} — run the scanner first to populate it",
        )
        return []

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT site_name, url, rationale, niche, quality_score, "
            "       deal_price, original_price, archived_at "
            "FROM deals "
            "ORDER BY archived_at DESC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export all vetted deals from the local DB to Google Sheets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Export only the N most recent deals (default: all)",
    )
    args = parser.parse_args()

    init_db()  # ensure schema exists even if DB is fresh

    deals = get_all_archived_deals(limit=args.limit)

    if not deals:
        print("No archived deals found to export.")
        logger.info("export_empty", "No archived deals found — nothing exported")
        return

    print(f"Exporting {len(deals)} archived deal(s) to Google Sheets…")
    logger.info(
        "export_start",
        f"Exporting {len(deals)} archived deals to Google Sheets",
        count=len(deals),
    )

    ok = append_deals(deals)
    if ok:
        print("✅ Export complete.")
        logger.info("export_done", "Export to Google Sheets completed successfully")
    else:
        print(
            "⚠️  Export failed or skipped — check logs and verify "
            "GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON are set correctly."
        )
        logger.warning("export_failed", "Export to Google Sheets failed or was skipped")


if __name__ == "__main__":
    main()
