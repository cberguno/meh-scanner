# Meh-Scanner

Daily deal-site discovery tool. Searches the web for **one-sale-a-day** / Meh-style stores, analyzes the current deal using AI, scores it for quality, and publishes vetted deals to a **Google Sheet** and a **GitHub Pages dashboard** automatically.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Project Structure](#project-structure)
3. [Quick Start (local)](#quick-start-local)
4. [Google Sheets Setup](#google-sheets-setup)
5. [GitHub Actions Setup](#github-actions-setup)
6. [Exporting Past Deals](#exporting-past-deals)
7. [Configuration Reference](#configuration-reference)
8. [Scheduling](#scheduling)

---

## How It Works

```
Search (Serper) → Enrich (Playwright) → Analyze (Claude AI)
    → Score → Filter (quality ≥ 6) → Vetted Deals
        → Google Sheet  (incremental, dedup by URL)
        → GitHub Pages dashboard  (public/index.html)
        → SQLite archive  (data/meh_scanner.db)
```

Each daily scan:
- Visits up to 25 candidate deal sites
- Extracts current deal title, price, brand, niche
- Scores quality on a 1–10 scale (AI-assisted)
- Appends **new** vetted deals (score ≥ 6) to a Google Sheet (skips URLs already in the sheet)

---

## Project Structure

```
meh-scanner/
├── main.py               # CLI entry point (search → analyze → export)
├── scanner.py            # Web-app scan pipeline entry point
├── scraper.py            # Playwright-based deal page scraper
├── analyzer.py           # Claude AI analysis
├── scoring.py            # Quality scoring / candidate filtering
├── sheets.py             # Google Sheets writer (service account auth)
├── export_to_sheets.py   # ★ Standalone: export all past deals to Sheets
├── db.py                 # SQLite persistence layer
├── config.py             # Environment variable configuration
├── dashboard_export.py   # Generates public/index.html + public/latest.json
├── logger.py             # Structured JSON logger
├── requirements.txt
├── .env.example          # Environment variable template
└── .github/workflows/
    └── daily-meh-scanner.yml  # GitHub Actions: daily scan + Pages deploy
```

---

## Quick Start (local)

```bash
# 1. Clone and install
git clone https://github.com/cberguno/meh-scanner
cd meh-scanner
pip install -r requirements.txt
playwright install chromium

# 2. Configure environment
cp .env.example .env
# Edit .env — add your API keys (see Configuration Reference below)

# 3. Run a scan
python main.py

# 4. Open the dashboard
open public/index.html
```

---

## Google Sheets Setup

Meh-Scanner uses a **Google Service Account** (no user interaction needed, works in CI) to write to your spreadsheet.

### Step 1 — Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (or select an existing one).
2. Enable the **Google Sheets API**:
   - Navigate to **APIs & Services → Library**
   - Search for "Google Sheets API" → click **Enable**

### Step 2 — Create a Service Account

1. Go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials → Service account**.
3. Give it a name (e.g. `meh-scanner`) and click **Done**.
4. Click on the service account you just created → **Keys** tab.
5. Click **Add Key → Create new key → JSON → Create**.
6. A JSON file downloads automatically — keep it safe, **don't commit it**.

### Step 3 — Create the Spreadsheet and share it

1. Create a new Google Sheet at <https://sheets.new>.
2. Copy the **spreadsheet ID** from the URL:
   ```
   https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
   ```
3. Click **Share** (top-right) and share the sheet with the service account's **email address** (found in the JSON file under `"client_email"`) as **Editor**.
4. Create a tab named **Deals** (the scanner writes to this tab; it will be created automatically on first write if you're using the API, but it's safer to create it manually).

### Step 4 — Set environment variables

Open your `.env` file and set:

```dotenv
GOOGLE_SHEET_ID=<your-spreadsheet-id>
GOOGLE_SERVICE_ACCOUNT_JSON=<paste the full JSON here, single-line>
```

**Tip — single-lining the JSON:**

```bash
# Mac / Linux
cat your-service-account.json | tr -d '\n'
```

Paste the result as the value of `GOOGLE_SERVICE_ACCOUNT_JSON`.

Alternatively, you can base64-encode it:

```bash
base64 -w 0 your-service-account.json   # Linux
base64 -i your-service-account.json     # Mac
```

Meh-Scanner accepts both raw JSON and base64-encoded JSON automatically.

### Step 5 — Verify

```bash
python export_to_sheets.py --limit 5
```

If successful you'll see `✅ Export complete.` and new rows in your sheet.

---

## GitHub Actions Setup

Add the following **repository secrets** (Settings → Secrets and variables → Actions → New repository secret):

| Secret name | Value |
|---|---|
| `SERPER_API_KEY` | Your [Serper.dev](https://serper.dev) API key |
| `ANTHROPIC_API_KEY` | Your [Anthropic](https://console.anthropic.com) API key |
| `GOOGLE_SHEET_ID` | Spreadsheet ID from Step 3 above |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service-account JSON (raw or base64) |

The daily workflow (`.github/workflows/daily-meh-scanner.yml`) runs automatically at **09:00 UTC** every day and can also be triggered manually via **Actions → Daily Meh-Scanner → Run workflow**.

---

## Exporting Past Deals

All vetted deals are archived in a local SQLite database (`data/meh_scanner.db`). If you want to backfill the Google Sheet with **all historical deals** (not just today's), run:

```bash
# Export all past vetted deals
python export_to_sheets.py

# Export only the 100 most recent
python export_to_sheets.py --limit 100
```

The script:
- Reads every deal that passed quality scoring from the local database
- Calls the same `append_deals()` function used during scans
- **Skips duplicates** — URLs already in the sheet are not re-added
- Supports incremental runs — safe to run repeatedly

---

## Configuration Reference

All settings are read from environment variables (or a `.env` file).

| Variable | Required | Description |
|---|---|---|
| `SERPER_API_KEY` | ✅ | [Serper.dev](https://serper.dev) search API key |
| `ANTHROPIC_API_KEY` | ✅ | [Anthropic Claude](https://console.anthropic.com) API key |
| `GOOGLE_SHEET_ID` | ✅ for Sheets | Spreadsheet ID from the Google Sheets URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ for Sheets | Service account credentials JSON (raw or base64) |
| `AMAZON_AFFILIATE_TAG` | ❌ | Amazon affiliate tag (e.g. `yourtag-20`); disables if blank |
| `TELEGRAM_BOT_TOKEN` | ❌ | Telegram bot token for high-score deal alerts |
| `TELEGRAM_CHAT_ID` | ❌ | Telegram chat/channel ID for alerts |
| `MEH_DASHBOARD` | ❌ | Set to `0` to skip HTML dashboard generation (default: `1`) |
| `MEH_DASHBOARD_DRY_RUN` | ❌ | Set to `1` to skip writing dashboard files (default: `0`) |
| `MEH_DASHBOARD_BASE_PATH` | ❌ | Base path for GitHub Pages (e.g. `/meh-scanner/`) |
| `MAX_CANDIDATES_PER_RUN` | ❌ | Max sites analyzed per run (default: `25`) |

---

## Scheduling

**GitHub Actions (recommended):** The included workflow runs every day at 09:00 UTC. Trigger it manually any time from the **Actions** tab.

**Cron (self-hosted):**

```cron
# Every day at 9 AM UTC
0 9 * * * cd /path/to/meh-scanner && python main.py >> logs/cron.log 2>&1
```

**Backfill / one-off export:**

```bash
python export_to_sheets.py
```
