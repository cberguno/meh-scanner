#!/usr/bin/env python3
"""
export_history.py — One-off script to publish ALL past meh-scanner deal data
to the configured Google Spreadsheet.

Usage
-----
    python export_history.py [--json [FILE]]

Options
-------
--json [FILE]   Also write a local JSON snapshot.  If FILE is omitted the
                output goes to ``data/history_export.json``.  Useful if you
                haven't set up Google Sheets credentials yet.

Requirements
------------
- GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON must be set in .env
  (or as real environment variables).
- The service account must have Editor access to the spreadsheet.
- Run ``python setup_sheets.py`` first if you don't have a sheet yet.

What it does
------------
1. Opens the local SQLite database at ``data/meh_scanner.db``.
2. Reads every row from the ``deals`` table (joined with ``scan_runs``).
3. Writes them to the ``History`` tab of your Google Sheet (tab is created
   automatically if it doesn't exist).
4. Optionally writes a local JSON file as a portable backup / audit trail.

The History tab is always **replaced** (not appended) so re-running this
script is safe and idempotent.

Data fields exported
--------------------
  ID, Site, URL, Niche, Score, Price, Was, Rationale, Archived At,
  Scan Run At
"""

import argparse
import json
import sys
from pathlib import Path

from db import init_db, get_all_deals
from sheets import publish_all_deals
from logger import logger


DEFAULT_JSON_PATH = Path("data") / "history_export.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish all past meh-scanner deal scans to Google Sheets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        nargs="?",
        const=str(DEFAULT_JSON_PATH),
        metavar="FILE",
        help=(
            "Also write a local JSON snapshot of the history "
            f"(default path: {DEFAULT_JSON_PATH})"
        ),
    )
    return parser.parse_args()


def _write_json(deals: list[dict], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(deals, fh, indent=2, default=str)
        tmp.replace(out)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"  ✅ JSON snapshot written → {out.resolve()}")


def main() -> int:
    args = _parse_args()

    print("📋 Meh-Scanner — Historical Deal Export")
    print("=" * 45)

    # ── 1. Initialise DB (creates tables if first run) ────────────────────────
    print("🗄️  Opening local database …")
    init_db()

    # ── 2. Fetch all historical deals ─────────────────────────────────────────
    print("🔍  Reading all archived deals from SQLite …")
    deals = get_all_deals()
    print(f"    Found {len(deals)} deal(s) across all scan runs.")

    if not deals:
        print("⚠️  No deals found in the database.  Run main.py first to populate data.")
        return 0

    # ── 3. Optionally write a local JSON backup ───────────────────────────────
    if args.json:
        print(f"💾  Writing JSON snapshot …")
        _write_json(deals, args.json)

    # ── 4. Publish to Google Sheets ───────────────────────────────────────────
    print("📊  Publishing to Google Sheets …")
    ok = publish_all_deals(deals)

    if ok:
        print(f"  ✅  {len(deals)} deal(s) written to the 'History' tab.")
        print()
        print("Done! Open your Google Sheet to view the full history.")
        return 0
    else:
        print()
        print("⚠️  Google Sheets publish failed (check logs above).")
        print("   Make sure GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON are set.")
        print("   See README.md → 'Google Sheets Setup' for instructions.")
        if not args.json:
            print()
            print("   Tip: run with --json to save a local backup regardless:")
            print("        python export_history.py --json")
        return 1


if __name__ == "__main__":
    sys.exit(main())
