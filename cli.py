"""
CLI entrypoint for meh-scanner.

Usage:
    python cli.py run-once

Runs one full scan, writes candidates.csv to the project root,
and prints the first 10 rows to stdout.
"""
import sys
from scanner import run_full_scan


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "run-once":
        print("Usage: python cli.py run-once")
        sys.exit(1)

    print("Running scan...")
    result = run_full_scan()

    if not result["success"]:
        print(f"Scan failed: {result.get('error')}")
        sys.exit(1)

    print(f"Done. {result['deals_count']} deals from {result['candidates']} candidates in {result['runtime']}s.")
    print("candidates.csv written to project root.")


if __name__ == "__main__":
    main()
