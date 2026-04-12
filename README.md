# Meh-Scanner

**Meh-Scanner** is an autonomous daily deal discovery and analysis tool that hunts for indie "one sale a day" / "one product a day" websites (Meh-style sites) and evaluates their current deals using AI-powered analysis.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [How It Works](#how-it-works)
3. [Data Persistence & Scan History](#data-persistence--scan-history)
4. [Google Sheets Setup](#google-sheets-setup)
5. [Exporting All Past Scans](#exporting-all-past-scans)
6. [Configuration Reference](#configuration-reference)
7. [Project Structure](#project-structure)
8. [Privacy & Merchant TOS](#privacy--merchant-tos)

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browsers
playwright install

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your API keys

# 4. Run a scan
python main.py
```

---

## How It Works

1. **Search Phase** — Queries Serper API for sites matching "one deal a day" keywords.
2. **Vibe Scoring** — Local heuristics filter for "Meh DNA" (personality, sarcasm, deal copy).
3. **Scraping Phase** — Playwright headless browser visits each candidate and extracts deal content.
4. **Analysis Phase** — Claude (Anthropic) scores each deal and writes a rationale.
5. **Filtering & Output** — Deals scoring ≥ 6 are kept and written to:
   - HTML dashboard (`public/index.html`)
   - JSON API (`public/latest.json`)
   - Google Sheets (optional, configured via env vars)
   - SQLite database (`data/meh_scanner.db`)

---

## Data Persistence & Scan History

### Where scan data is stored

All scan results are persisted locally in a **SQLite database** at:

```
data/meh_scanner.db
```

The database contains three key tables:

| Table | What it stores |
|-------|----------------|
| `scan_runs` | One row per scan run: timestamp, success flag, deal count, runtime |
| `deals` | Every deal ever found: site name, URL, niche, quality score, price, rationale, archived timestamp |
| `source_stats` | Per-domain visit history and quality scoring |

The database is created automatically on first run and **retains all historical data** — no data is overwritten or deleted between runs.

### What gets written to Google Sheets

Each scan run appends new deals to the **"Deals"** tab (duplicates are skipped by URL).
The **"History"** tab is a complete snapshot of all past scans, written by the
`export_history.py` script (see [Exporting All Past Scans](#exporting-all-past-scans)).

---

## Google Sheets Setup

Meh-Scanner uses a **service account** (not OAuth) to write to Google Sheets.
This allows the script to run unattended in CI/CD without browser-based OAuth prompts.

### Step 1 — Create a Google Cloud project and enable APIs

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and create a project (or select an existing one).
2. Enable **Google Sheets API**:  
   `APIs & Services → Library → Google Sheets API → Enable`

### Step 2 — Create a service account

1. Go to `APIs & Services → Credentials → Create Credentials → Service Account`.
2. Give it a name (e.g. `meh-scanner-sheets`).
3. Skip optional steps and click **Done**.
4. Click the service account → **Keys** tab → **Add Key → Create new key → JSON**.
5. Download the JSON file — keep it secret, never commit it.

### Step 3 — Set credentials in your environment

**Option A — raw JSON (recommended for local dev):**

```bash
# In .env (or export in your shell):
GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account","project_id":"my-project",...}'
```

Copy the entire contents of the downloaded JSON file as the value.

**Option B — base64-encoded (recommended for CI secrets):**

```bash
# Linux (GNU coreutils):
base64 -w 0 my-service-account.json

# macOS (BSD base64):
base64 -i my-service-account.json

# Paste the output as the value of GOOGLE_SERVICE_ACCOUNT_JSON
```

### Step 4 — Create the spreadsheet and share it

**Option A — automated (creates sheet + updates .env):**

```bash
python setup_sheets.py
```

This runs a one-time OAuth flow (separate from the service account) to create the
spreadsheet, then saves the `GOOGLE_SHEET_ID` to your `.env`.

**Option B — manual:**

1. Create a Google Sheet at [sheets.google.com](https://sheets.google.com).
2. Copy the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/**<SHEET_ID>**/edit`
3. Set `GOOGLE_SHEET_ID=<SHEET_ID>` in your `.env`.
4. **Share** the spreadsheet with the service account email (shown in the JSON under `client_email`) as **Editor**.

> ⚠️ **The share step is required.** Without it the service account cannot write to the sheet.

### Step 5 — Verify

Run a scan or the export script and check that data appears in the sheet:

```bash
python export_history.py --json   # export history + local JSON backup
```

---

## Exporting All Past Scans

Use `export_history.py` to publish your **complete deal history** to Google Sheets.

```bash
# Export to Google Sheets (requires GOOGLE_SHEET_ID + GOOGLE_SERVICE_ACCOUNT_JSON)
python export_history.py

# Also write a local JSON backup (useful if Sheets credentials aren't set up yet)
python export_history.py --json

# Write JSON to a custom path
python export_history.py --json /tmp/all_deals.json
```

### What it does

1. Opens `data/meh_scanner.db` and reads **every deal** ever archived.
2. Writes them to the **"History"** tab of your spreadsheet (creating the tab if needed).
3. The History tab is **replaced** on every run — re-running is safe and idempotent.

### JSON export format

Each deal in the JSON export contains:

```json
{
  "id": 42,
  "site_name": "Meh",
  "url": "https://meh.com/deals/some-product",
  "niche": "Electronics",
  "quality_score": 8.5,
  "deal_price": "$29.99",
  "original_price": "$59.99",
  "rationale": "High-quality daily deal with strong brand ...",
  "archived_at": "2026-04-10 14:30:00",
  "scan_timestamp": "2026-04-10 14:25:00"
}
```

### Scheduled exports

To keep the History tab automatically up-to-date, add the export script to your
GitHub Actions workflow after each scan:

```yaml
# In .github/workflows/daily-meh-scanner.yml (example addition):
- name: Export full history to Google Sheets
  run: python export_history.py
  env:
    GOOGLE_SHEET_ID: ${{ secrets.GOOGLE_SHEET_ID }}
    GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
```

---

## Configuration Reference

All settings are loaded from environment variables (`.env` file or shell exports).

| Variable | Required | Description |
|----------|----------|-------------|
| `SERPER_API_KEY` | ✅ | Serper.dev API key for web search |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key for Claude analysis |
| `GOOGLE_SHEET_ID` | Optional | Target spreadsheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Optional | Service account credentials (raw JSON or base64) |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram bot token for deal alerts |
| `TELEGRAM_CHAT_ID` | Optional | Telegram chat ID for deal alerts |
| `AMAZON_AFFILIATE_TAG` | Optional | Amazon affiliate tag (appended to Amazon URLs) |
| `MEH_DASHBOARD` | Optional | Set to `0` to disable HTML dashboard generation |
| `MEH_DASHBOARD_DRY_RUN` | Optional | Set to `1` to log dashboard intent without writing files |
| `MEH_DASHBOARD_BASE_PATH` | Optional | Base path for GitHub Pages deployment (e.g. `/meh-scanner/`) |

---

## Project Structure

```
meh-scanner/
├── main.py                # Entry point — CLI and scheduled run
├── scanner.py             # Full scan pipeline (search → scrape → analyze → export)
├── scraper.py             # Serper search + Playwright scraping
├── analyzer.py            # Claude AI analysis (batch)
├── scoring.py             # Deal quality scoring
├── sheets.py              # Google Sheets integration (append + history export)
├── db.py                  # SQLite persistence layer
├── dashboard_export.py    # Static HTML/JSON dashboard generator
├── export_history.py      # One-off script: publish all past scans to Google Sheets
├── setup_sheets.py        # One-time setup: create spreadsheet + update .env
├── config.py              # Centralised configuration
├── logger.py              # Structured logging
├── affiliate.py           # Affiliate URL helpers
├── alerts.py              # Telegram deal alerts
├── app.py                 # FastAPI web dashboard (optional)
├── cli.py                 # CLI helpers
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
├── data/
│   └── meh_scanner.db     # SQLite database (all past scans — auto-created)
├── public/                # Generated static files (dashboard + JSON API)
├── logs/                  # JSON + console logs (daily rotation)
└── docs/                  # Additional documentation
```

---

## Privacy & Merchant TOS

- **Robots.txt** — The scraper respects `robots.txt` where possible and uses
  polite delays between requests.
- **No PII collected** — Meh-Scanner records deal prices, product titles, and
  URLs. No user or customer data is stored.
- **Affiliate links** — Set `AMAZON_AFFILIATE_TAG` to append your affiliate tag
  to Amazon links. Leave blank to use plain URLs.
- **Rate limits** — Serper.dev and Anthropic usage is bounded at 25 candidates
  per run to control costs and avoid aggressive crawling.
- **Google Sheets data** — The spreadsheet contains only deal metadata scraped
  from public product pages. Share the sheet only with trusted collaborators.
