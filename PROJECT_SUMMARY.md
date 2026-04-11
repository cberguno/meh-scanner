# Meh-Scanner: Complete Project Summary

**For:** Developers building features on meh-scanner or integrating with external systems (e.g., HTTP dashboards, email summaries, affiliate tracking)

**Last Updated:** April 2026

---

## 1. Project Overview

### What is Meh-Scanner?

**Meh-Scanner** is an autonomous daily deal discovery and analysis tool that hunts for indie "one sale a day" / "one product a day" websites (Meh-style sites) and evaluates their current deals using AI-powered analysis.

### Problem & Purpose

- **Problem:** Indie daily deal sites with personality (like Meh.com) are hard to discover and keep track of. Marketplaces (Amazon, Woot, Groupon) are noise.
- **Solution:** Automated search + AI analysis pipeline that finds promising deal sites, scrapes their current offers, scores quality, and presents findings in a clean dashboard.
- **Audience:** Deal hunters, indie deal site enthusiasts, researchers tracking niche commerce trends.

### High-Level Functionality

1. **Search Phase** — Query search engines (Serper API) for sites matching "one deal a day" keywords
2. **Vibe Scoring** — Local heuristics filter for "Meh DNA" (sarcasm, humor, personality keywords)
3. **Scraping Phase** — Visit each candidate site, extract deal page content (Playwright + HTTP fallback)
4. **Analysis Phase** — Send scraped content to Claude (Anthropic) for scoring and rationale
5. **Filtering & Output** — Keep deals scoring ≥6, log events, export to HTML dashboard + JSON API + Google Sheets
6. **Deployment** — Static HTML/JSON published to GitHub Pages on schedule

### Core Value Proposition

- **Low-cost automation:** Uses cheaper Claude model (Haiku), efficient batching, request deduplication (SQLite)
- **High-quality curation:** Combines keyword heuristics + AI judgment to avoid false positives
- **Open exploration:** Finds new sites daily; maintains seen-site DB to avoid re-analyzing old URLs
- **Transparent scoring:** Each deal includes rationale (why it's good), niche, price signals, quality score

---

## 2. Project Structure

```
meh-scanner/
├── main.py                      # Entry point; orchestrates search → analyze → export
├── config.py                    # Configuration (env vars, search queries, safety limits)
├── scraper.py                   # Search for deal sites + scrape deal pages (Playwright + HTTP)
├── analyzer.py                  # AI analysis via Claude API (parallel batch processing)
├── dashboard_export.py          # Generate public/index.html + public/latest.json
├── logger.py                    # Structured logging (console + JSON file logs)
├── sheets.py                    # Google Sheets integration (stub; append deals to sheet)
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
├── .github/workflows/
│   └── daily-meh-scanner.yml    # GitHub Actions: daily schedule + deploy to GitHub Pages
├── public/                      # Output directory (generated)
│   ├── index.html              # Interactive HTML dashboard
│   ├── latest.json             # JSON API with latest deals + metadata
│   └── .nojekyll               # Disables Jekyll (critical for GitHub Pages)
├── logs/                        # JSON + console logs (daily file rotation)
├── GITHUB_PAGES_SETUP.md        # Guide for GitHub Pages deployment
├── DEPLOY_CHECKLIST.md          # Pre-deployment verification steps
└── test_github_pages.sh         # Local test script (base path + file generation)
```

### Key Modules & Responsibilities

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates the full pipeline: search → enrich → analyze → filter → export. Handles error recovery. |
| `config.py` | Centralizes env vars, API keys, search queries, safety limits (max 25 candidates/run). |
| `scraper.py` | (1) Search via Serper API; (2) Scrape deal pages with Playwright (or HTTP fallback); (3) SQLite dedup to avoid re-scraping. |
| `analyzer.py` | Parallel batch analysis using Claude API. Returns JSON with `rationale`, `quality_score` (0–10), `niche`. |
| `dashboard_export.py` | Generates static `public/index.html` (dark-mode table, filters, sorting) + `public/latest.json` API. Injects base path for GitHub Pages. |
| `logger.py` | Structured logging: console (colored text) + file (JSON). Logs events like `search_phase`, `deal_detail`, `dashboard_written`. |
| `sheets.py` | Google Sheets API integration (append deals to a shared sheet for backup/collaboration). |

---

## 3. Core Architecture & Data Flow

### Execution Flow: `python main.py`

```
1. main() starts
   ├─ Check config: Validate SERPER_API_KEY, ANTHROPIC_API_KEY
   │
   ├─ Search phase (scraper.py)
   │  ├─ Run 7 search queries (hardcoded in Config.SEARCH_QUERIES)
   │  ├─ Serper API returns ~3–5 candidates per query
   │  ├─ Filter by "Meh vibe score" (keywords like "witty", "sarcastic", "one deal")
   │  └─ Limit to MAX_CANDIDATES_PER_RUN (25) to keep costs low
   │
   ├─ Enrichment phase (scraper.py)
   │  ├─ For each candidate URL:
   │  │  ├─ Check SQLite `seen_sites` to avoid re-scraping
   │  │  ├─ Try Playwright (headless browser) to extract deal page content
   │  │  ├─ Fallback to HTTP + BeautifulSoup if Playwright times out
   │  │  ├─ Extract: deal title, price signals, promo copy, meh keywords
   │  │  └─ Mark URL as seen in SQLite
   │  └─ Return enriched site objects with deal context
   │
   ├─ Analysis phase (analyzer.py)
   │  ├─ Batch 10 sites in parallel (ThreadPoolExecutor)
   │  ├─ For each site, call Claude Haiku with:
   │  │  └─ Search result (title, snippet) + scraped page content
   │  ├─ Claude returns JSON: { "rationale": "...", "quality_score": 7, "niche": "Tech" }
   │  └─ Log each analysis result
   │
   ├─ Filter phase (main.py)
   │  ├─ Keep only deals where quality_score >= 6
   │  ├─ Discard low-quality or non-deal sites
   │  └─ Build final deals list
   │
   ├─ Export phase (dashboard_export.py)
   │  ├─ Generate public/index.html:
   │  │  ├─ Dark-mode table (7 columns: Score, Site, Niche, Price, Was/MSRP, Rationale, URL)
   │  │  ├─ Sortable headers + search filter + min-score dropdown
   │  │  ├─ Color-coded rows (green ≥8, blue ≥7, default <7)
   │  │  └─ Inject <base href> for GitHub Pages if MEH_DASHBOARD_BASE_PATH is set
   │  ├─ Generate public/latest.json:
   │  │  └─ JSON API: { generated_at, candidates_scanned, deals_count, deals[] }
   │  ├─ Create public/.nojekyll (empty file, disables Jekyll)
   │  └─ Log success/warnings
   │
   └─ Logging phase (logger.py)
      └─ Write JSON logs to logs/meh-scanner-YYYY-MM-DD.log
```

### The "Deal" Data Structure

Every deal is a dictionary with these keys:

```python
{
    "site_name": str,           # e.g., "Baffect.com"
    "url": str,                 # Direct link to the site
    "rationale": str,           # Why Claude thinks it's good (1–2 sentences)
    "niche": str,               # Category (e.g., "Electronics", "Home Goods")
    "quality_score": int/float, # 0–10 (only deals ≥6 kept)
    "deal_price": str,          # Optional: current deal price (e.g., "$19.99")
    "original_price": str,      # Optional: original/MSRP (e.g., "$49.99")
}
```

#### Example Deal

```json
{
  "site_name": "Baffect",
  "url": "https://baffect.com",
  "rationale": "Daily deal on mystery tech gadgets with witty descriptions. Fresh inventory, reasonable prices, consistent personality.",
  "niche": "Electronics",
  "quality_score": 8,
  "deal_price": "$19.99",
  "original_price": "$49.99"
}
```

### Discovery, Filtering, Scoring Logic

1. **Search Queries** (7 hardcoded, in `config.py`):
   - `"one deal a day" OR "single daily deal" -groupon -slickdeals -woot ...` (excludes big marketplaces)
   - `"meh style" OR "meh.com style" daily deal ...`
   - `"witty OR cynical OR sarcastic OR humorous" + "daily deal"` (personality signals)

2. **Vibe Scoring** (local heuristic in `scraper.py`, pre-filter before LLM):
   - Keywords: "one deal", "daily deal", "witty", "sarcastic", "snark", "flash sale", etc.
   - Score ≥ 3 passes to analysis; lower scores rejected early to save API calls

3. **Quality Scoring** (Claude LLM in `analyzer.py`):
   - Claude reads: search result + scraped page content
   - Asks: "Is this a strong Meh-like daily deal experience?"
   - Returns: `quality_score` (0–10)
   - Main pipeline keeps ≥6; export further highlights ≥7 (blue) and ≥8 (green)

4. **Deduplication**:
   - SQLite `seen_sites.db` tracks URLs (normalized, lowercase, no query params)
   - Prevents re-scraping same URL within 120 days
   - Keeps database small: auto-deletes entries >120 days old

---

## 4. Logging & Observability

### Logging Library

- **Standard Library:** Python's `logging` module
- **Custom Wrapper:** `StructuredLogger` class in `logger.py`
- **Format:**
  - **Console:** Colored, human-readable (e.g., `[INFO] 2026-04-11 09:15:23 Meh-Scanner starting... event=run_started`)
  - **File:** JSON (e.g., `{"timestamp": "2026-04-11T09:15:23", "level": "INFO", "message": "...", "event": "run_started", ...}`)

### Key Log Events

| Event | Meaning | Example Call |
|-------|---------|--------------|
| `run_started` | Scan begins | `logger.info("run_started", message="Meh-Scanner starting...")` |
| `search_phase` | Search queries running | `logger.info("search_phase", message="Starting search for deal sites...")` |
| `search_completed` | Search done; N candidates found | `log_search_complete(candidates_found=5, vibe_threshold=3)` |
| `site_scraped` | Successful page scrape | `log_site_scraped(url, vibe_score=6)` |
| `deal_detail` | **[RECENT FIX]** Logging individual deal found | `logger.info("deal_detail", message=f"{deal['site_name']} (score {deal['quality_score']})", site_name=..., url=..., niche=..., quality_score=...)` |
| `deals_found` | N deals passed quality filter | `logger.info("deals_found", count=5, message="Found 5 potential deals")` |
| `analysis_started` | AI analysis begins | `log_analysis_start(sites_count=5)` |
| `site_analysis_failed` | Claude API error for a site | `log_site_analyzed(url, quality_score=0, success=False, error="...")` |
| `dashboard_written` | Export complete | Logs path, deals count, base path, any warnings |
| `run_completed` | Scan finished | `log_run_summary(candidates=5, deals=2, errors=0, runtime=12.3)` |

### Recent Logging Fix (April 2026)

**Issue:** `logger.info()` call at line 73 in `main.py` was missing the required `message=` parameter.

**Fix Applied:**
```python
# Before (error):
logger.info("deal_detail", 
           site_name=deal['site_name'], 
           url=deal['url'],
           ...)  # Missing message=

# After (correct):
logger.info("deal_detail",
           message=f"{deal['site_name']} (score {deal['quality_score']})",
           site_name=deal['site_name'],
           url=deal['url'],
           ...)
```

**Signature:** `StructuredLogger.info(event: str, message: str, **context)`
- `event`: Short event name (e.g., `"deal_detail"`)
- `message`: Human-readable summary
- `**context`: Additional key-value pairs (logged in JSON file and console)

### Log Locations

- **Console:** Real-time colored output to `stdout` during run
- **File:** `logs/meh-scanner-YYYY-MM-DD.log` (JSON format, one entry per line)
- **Workflow Logs:** GitHub Actions summary (configurable verbosity)

---

## 5. Configuration & Dependencies

### Environment Variables

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `SERPER_API_KEY` | ✅ Yes | — | Serper.dev search API key |
| `ANTHROPIC_API_KEY` | ✅ Yes | — | Anthropic Claude API key |
| `GITHUB_API_KEY` | ❌ No | — | GitHub API (future use; currently unused) |
| `GOOGLE_SHEET_ID` | ❌ No | — | Google Sheet ID for appending deals |
| `MEH_DASHBOARD` | ❌ No | `1` | Enable/disable dashboard export (1 = yes, 0 = no) |
| `MEH_DASHBOARD_DRY_RUN` | ❌ No | `0` | Log intent without writing files (for testing) |
| `MEH_DASHBOARD_BASE_PATH` | ❌ No | `""` | Base path for GitHub Pages (e.g., `/meh-scanner/`; auto-set by CI) |

### Configuration File

- **Location:** `.env` (copied from `.env.example`, not committed to git)
- **Loaded via:** `python-dotenv` in `config.py`

### Main Dependencies

```
playwright          # Headless browser automation (Chromium)
anthropic           # Claude API client
google-api-python-client
google-auth         # Google Sheets integration
google-auth-oauthlib
python-dotenv       # .env file loader
pandas              # Data manipulation (utilities)
requests            # HTTP client
tenacity            # Retry logic (exponential backoff)
beautifulsoup4      # HTML parsing (BS4)
```

### External Services & Rate Limits

| Service | Usage | Limit | Cost |
|---------|-------|-------|------|
| **Serper.dev** | Web search (7 queries × 3–5 results) | 100 free/month; ~$0.008/query | ~$0.05–0.10/run |
| **Anthropic Claude (Haiku)** | Site analysis (batch of ~25 × Haiku model) | Pay-as-you-go | ~$0.05–0.15/run |
| **Playwright** | Headless browser (local; no API limit) | — | Free (local resource) |
| **Google Sheets** | Append deals (optional) | — | Free (API quota) |

---

## 6. Key Functions & Extensibility

### Top-Level Functions

#### `main.py`

```python
def main():
    """Orchestrate full pipeline: search → enrich → analyze → filter → export"""
```
- Calls `search_for_deal_sites()` → `enrich_candidates()` → `analyze_sites_batch()` → filter deals → `export_daily_dashboard()`
- Returns nothing; logs all results and handles errors

#### `scraper.py`

```python
def search_for_deal_sites() -> list[dict]:
    """Query Serper API with hardcoded search queries; return candidate sites (title, link, snippet, vibe_score)"""

def enrich_candidates(sites: list[dict]) -> list[dict]:
    """For each site, scrape deal page; return sites with added fields: deal_title, deal_price, promo_copy, meh_signals, screenshot_path"""

def score_meh_vibe(title: str, snippet: str) -> float:
    """Keyword-based scoring (0–10) for 'Meh DNA' — local heuristic pre-filter"""

def scrape_deal_page(url: str) -> dict:
    """Scrape one deal page (Playwright with fallback to HTTP). Return: deal_title, deal_price, promo_copy, meh_signals"""
```

#### `analyzer.py`

```python
def analyze_site(site: dict) -> str:
    """Send site context to Claude Haiku; return JSON string with rationale, quality_score, niche"""

def analyze_sites_batch(sites: list[dict], max_workers=10) -> list[dict]:
    """Parallel batch analysis (ThreadPoolExecutor). Return list of {site, analysis} dicts"""
```

#### `dashboard_export.py`

```python
def export_daily_dashboard(deals: list[dict], candidates_count: int, runtime_seconds: float) -> None:
    """Generate public/index.html + public/latest.json + public/.nojekyll. Handle base path injection."""
```

### Where to Extend or Modify

#### Add a New Search Query
- **File:** `config.py`, line ~25
- **Action:** Append a new string to `Config.SEARCH_QUERIES`
- **Effect:** Next run will include this query in Serper API calls

#### Change Quality Score Threshold
- **File:** `main.py`, line ~49
- **Current:** `if parsed.get('quality_score', 0) >= 6:`
- **Action:** Change `6` to desired threshold (e.g., `7` for stricter filtering)

#### Modify Dashboard Appearance
- **File:** `dashboard_export.py`, line ~165 (CSS) and ~200 (HTML table structure)
- **Action:** Edit CSS styles (colors, fonts, layout) or table columns (add/remove `<th>` and `<td>`)

#### Add Affiliate Link Rewriting
- **File:** Create `affiliate_rewriter.py` or modify `main.py` before `export_daily_dashboard()`
- **Pattern:** For each deal, rewrite `deal['url']` using an affiliate link provider (e.g., `affiliate_link(original_url)`)
- **Integration:** Call before dashboard export; deals already have URLs

#### Integrate Email Summaries
- **File:** Create `email_sender.py`
- **Pattern:** After `export_daily_dashboard()`, read `public/latest.json`, format a summary email, send via SMTP
- **Trigger:** Daily via GitHub Actions workflow or a separate cron job

#### Add Database Persistence (Instead of JSON)
- **Current:** In-memory + file-based (`public/latest.json`)
- **Alternative:** Replace `dashboard_export.py` with a function that writes to PostgreSQL/SQLite
- **Impact:** Requires updating `/api/*` endpoints (if adding HTTP dashboard) to query DB instead of JSON files

### Design Patterns

1. **Retry Logic (Tenacity):** Network calls (search, scrape, LLM) use `@retry` decorator with exponential backoff
2. **Parallel Processing (ThreadPoolExecutor):** Analysis batch processes up to 10 sites concurrently
3. **Error Recovery:** Most failures log and continue; missing a site doesn't crash the whole run
4. **Deduplication (SQLite):** Avoids redundant API calls and network overhead
5. **Structured Logging:** Every major step logs an event; enables post-mortem analysis and debugging

### Ease of Modification

| Change Type | Difficulty | Notes |
|-------------|------------|-------|
| **Search queries** | 🟢 Easy | Add to `Config.SEARCH_QUERIES` |
| **Quality threshold** | 🟢 Easy | Change comparison in `main.py` |
| **Dashboard styling** | 🟢 Easy | Edit CSS in `dashboard_export.py` |
| **Logging output** | 🟢 Easy | Modify `StructuredFormatter.format()` |
| **API integration (Google Sheets, Slack, etc.)** | 🟡 Medium | Add new module, call from `main()` |
| **Database swaps** | 🟡 Medium | Requires refactoring `export_daily_dashboard()` and adding new endpoints |
| **LLM model swap** | 🟡 Medium | Change `model=` in `analyzer.py`; may need prompt tuning |
| **Scraping strategy** | 🟠 Hard | Major rework of `scraper.py` + testing |

---

## 7. Current State, Limitations & Recent Changes

### Recent Changes (April 2026)

1. **Logging Fix:** Added missing `message=` parameter to `logger.info("deal_detail", ...)` call in `main.py` line 73.
   - This was blocking execution; now correctly logs each deal found with site name and score.

2. **GitHub Pages Deployment Hardening:**
   - Added explicit `.nojekyll` creation and verification in `dashboard_export.py`
   - Added base path injection logic for GitHub project sites (`MEH_DASHBOARD_BASE_PATH`)
   - Workflow now verifies all three files (index.html, latest.json, .nojekyll) before deploying
   - Friendly zero-deal fallback message (instead of blank table)

3. **Workflow Improvements:**
   - Enhanced "Check dashboard output" step with explicit file presence checks
   - "Visit live site" clickable link in workflow summary
   - First-time setup checklist in deployment summary

### Known Limitations

1. **Playwright Overhead:** Browser startup + navigation can be slow; HTTP fallback helps but reduces data quality
2. **API Costs:** Each run costs ~$0.10–0.20 (Serper + Claude). Daily runs = ~$3–6/month; acceptable for a hobby tool
3. **Search Scope:** Limited to first 3–5 results per query (Serper default); new indie sites take time to surface
4. **Vibe Heuristic:** Keyword-based pre-filter (60+ keywords) is fragile; some false positives/negatives
5. **No Database:** Deals stored only in JSON and (optionally) Google Sheets; no historical trending/analytics
6. **Rate Limiting:** Serper has 100 free searches/month; once exceeded, requires paid API key
7. **Deduplication TTL:** Seen sites expire after 120 days, so old sites may re-appear; tunable but not dynamic

### Error Handling Approach

- **Search fails:** Log error, exit early (no point analyzing if search returned 0 sites)
- **Single site scrape fails:** Log error, skip that site, continue with others (partial results OK)
- **Single site analysis fails:** Log error with site URL, use fallback score (5); include in output if no critical issues
- **Dashboard export fails:** Log error, but run still completes; critical for CI/CD reliability
- **Network timeouts:** Tenacity retry 3×with exponential backoff (2s → 10s)

### Code Quality & Maintainability

- **Strengths:**
  - Structured logging everywhere (easy to debug)
  - Modular functions (scraper, analyzer, export are separate)
  - Type hints in most functions
  - Clear config constants (easy to tweak)
  
- **Areas for Improvement:**
  - `scraper.py` is large (~500 lines); could split into sub-modules (search, scrape_playwright, scrape_http, etc.)
  - `dashboard_export.py` mixes HTML generation with file I/O; could use Jinja2 templating for cleaner UI updates
  - No unit tests; currently relying on integration testing via GitHub Actions
  - Error messages could be more specific (e.g., which Playwright step failed)

---

## 8. How to Run & Test

### Prerequisites

1. **Python 3.12+**
2. **API Keys:**
   - `SERPER_API_KEY` from [serper.dev](https://serper.dev)
   - `ANTHROPIC_API_KEY` from [Anthropic Console](https://console.anthropic.com)
   - Optionally: `GOOGLE_SHEET_ID` for sheets integration

### Installation

```bash
# Clone/navigate to project
cd meh-scanner

# Install dependencies
python -m pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Copy .env.example to .env and fill in your API keys
cp .env.example .env
# Edit .env with your actual keys
```

### Running a Scan

```bash
# Standard run (will search, analyze, and export to public/)
python main.py

# Dry-run (log intent without writing files)
MEH_DASHBOARD_DRY_RUN=1 python main.py

# Run with base path (for GitHub Pages project site)
MEH_DASHBOARD_BASE_PATH="/meh-scanner/" python main.py
```

### Expected Behavior

#### If Deals Are Found (Normal Case)

```
[INFO] 2026-04-11 09:15:23 Meh-Scanner starting... event=run_started
[INFO] 2026-04-11 09:15:24 All modules loaded successfully! event=config_loaded
[INFO] 2026-04-11 09:15:24 Starting search for deal sites... event=search_phase
[INFO] 2026-04-11 09:15:28 Search completed: 5 candidates found (vibe score >= 3) candidates_found=5 vibe_threshold=3 event=search_completed
[INFO] 2026-04-11 09:16:00 Found 5 potential deals count=5 event=deals_found
[INFO] 2026-04-11 09:16:00 Baffect (score 8) site_name=Baffect url=https://baffect.com niche=Electronics quality_score=8 event=deal_detail
[INFO] 2026-04-11 09:16:00 SomeOtherSite (score 7) ...
...
[INFO] 2026-04-11 09:16:05 Dashboard exported successfully path=public/index.html json_path=public/latest.json deals_count=2 event=dashboard_written
[INFO] 2026-04-11 09:16:05 After GitHub Pages deploy, open: https://username.github.io/meh-scanner/ pages_url=https://username.github.io/meh-scanner/ event=dashboard_pages_url
[INFO] 2026-04-11 09:16:05 Scan complete! event=run_completed
```

**Output Files:**
- `public/index.html` — Interactive dark-mode dashboard (~3–5 KB)
- `public/latest.json` — JSON API (~1–2 KB)
- `public/.nojekyll` — Empty file (0 bytes)
- `logs/meh-scanner-2026-04-11.log` — JSON event log

#### If Zero Deals Found

```
[INFO] 2026-04-11 09:15:24 Starting search for deal sites... event=search_phase
...
[WARNING] 2026-04-11 09:16:00 No deals passed quality threshold event=no_deals_passed
[INFO] 2026-04-11 09:16:05 Dashboard exported successfully deals_count=0 event=dashboard_written
```

**Dashboard Still Generated:**
- Table shows: "Hey there! 👋 No deals matching your current filters right now. Want to loosen things up a bit, try a different view, or let me help you explore some options? I'm here whenever you're ready! 📦"

### Testing Locally

#### Test 1: Verify Environment & Dependencies

```bash
python -c "from config import Config; print(f'SERPER_API_KEY: {bool(Config.SERPER_API_KEY)}'); print(f'ANTHROPIC_API_KEY: {bool(Config.ANTHROPIC_API_KEY)}')"
```

#### Test 2: Run Full Pipeline

```bash
python main.py
```

- Check `logs/meh-scanner-YYYY-MM-DD.log` for JSON events
- Open `public/index.html` in a browser
- Verify table loads, filters work, sorting works

#### Test 3: GitHub Pages Simulation

```bash
export MEH_DASHBOARD_BASE_PATH="/meh-scanner/"
python main.py
grep '<base href' public/index.html  # Should show: <base href="/meh-scanner/">
grep './latest.json' public/index.html  # Should show relative link
```

#### Test 4: Zero-Deal Message

```bash
# Manually test by editing main.py to filter with quality_score >= 10 (will find zero deals)
# Or wait for a run with no qualifying deals
# Then check public/index.html for the friendly message
```

#### Test 5: Local Web Server (Test Dashboard Locally)

```bash
cd public
python -m http.server 8000
# Open http://localhost:8000/index.html in browser
# Verify table, filters, sorting, links work
```

### GitHub Actions Deployment

- **Trigger:** Daily at 9 AM UTC, or manual trigger via Actions tab
- **Environment:** Ubuntu latest
- **Steps:**
  1. Checkout code
  2. Set up Python 3.12
  3. Install dependencies (uv)
  4. Run `python main.py`
  5. Check dashboard files
  6. Deploy to gh-pages branch
  7. Post summary to GitHub Actions

---

## 9. Dashboard Integration Notes

If you're adding an HTTP dashboard (e.g., FastAPI + Jinja2 + HTMX) on top of meh-scanner, here are 5 practical recommendations:

### 1. **Expose Deals via JSON API**

- **Recommendation:** Rather than hard-coding deals in the dashboard, export to `public/latest.json` (already done!) and read from there.
- **Implementation:** In FastAPI, create a `/api/deals` endpoint that:
  - Reads `public/latest.json`
  - Parses the deals array
  - Returns as JSON (or renders HTML for HTMX)
- **Benefit:** Decouples dashboard from scanner logic; scanner doesn't care if dashboard is HTTP vs. static files.
- **Code Pattern:**
  ```python
  @app.get("/api/deals")
  def get_deals():
      with open("public/latest.json") as f:
          data = json.load(f)
      return {"deals": data["deals"], "last_scan": data["generated_at"]}
  ```

### 2. **Trigger Scans from Dashboard (POST /api/scan)**

- **Recommendation:** Import scanner functions directly into FastAPI app (or subprocess the main.py); let dashboard button trigger a new scan.
- **Implementation:**
  - Button in dashboard → `POST /api/scan`
  - Backend runs `main()` in a thread or subprocess
  - Returns immediately with status; dashboard polls for results
- **Benefit:** No need to wait for daily schedule; run on-demand.
- **Caveat:** API costs money; guard with rate limiting (e.g., 1 scan/hour max).
- **Code Pattern:**
  ```python
  @app.post("/api/scan")
  def trigger_scan(background_tasks):
      background_tasks.add_task(run_scanner_in_bg)
      return {"status": "started"}
  ```

### 3. **Use Jinja2 for Dashboard Templates**

- **Recommendation:** Replace inline HTML in `dashboard_export.py` with Jinja2 templates.
- **Implementation:**
  - Create `templates/dashboard.html` with Jinja2 syntax
  - In `export_daily_dashboard()`, render: `env.get_template("dashboard.html").render(deals=deals, ...)`
  - Or in FastAPI: `templates.TemplateResponse("dashboard.html", {"request": request, "deals": deals})`
- **Benefit:** Easier to maintain UI (separate markup from Python logic); can share templates between static + HTTP dashboards.
- **File Structure:**
  ```
  templates/
  ├── dashboard.html       # Main dashboard
  ├── components/
  │   ├── deal_table.html  # Table component
  │   └── stats_card.html  # Metrics card
  ```

### 4. **Cache & Minimize API Calls**

- **Recommendation:** Cache `public/latest.json` in memory (Python dict); refresh only on `/api/scan` success.
- **Implementation:**
  ```python
  cache = {"deals": None, "last_updated": None}
  
  def load_deals():
      with open("public/latest.json") as f:
          cache["deals"] = json.load(f)
          cache["last_updated"] = datetime.now()
  ```
- **Benefit:** Dashboard is fast; no repeated disk I/O.

### 5. **Add Lightweight Metrics/Stats Endpoint**

- **Recommendation:** Create `GET /api/metrics` that returns high-level stats (not individual deals).
- **Implementation:**
  ```python
  @app.get("/api/metrics")
  def get_metrics():
      return {
          "total_deals": len(cache["deals"]),
          "avg_score": sum(d["quality_score"] for d in cache["deals"]) / len(cache["deals"]),
          "last_scan": cache["last_updated"],
          "scan_in_progress": False
      }
  ```
- **Benefit:** Dashboard can show status cards (e.g., "5 deals today, avg score 7.2") without rendering full table; great for mobile/dashboards.

### Suggested Stack for HTTP Dashboard

```
FastAPI          # Lightweight async web framework
uvicorn          # ASGI server
Jinja2           # Templates
HTMX             # Dynamic updates without page reload
Tailwind CSS     # Styling (can replace inline styles from dashboard_export.py)
```

**Why This Stack?**
- Minimal dependencies; matches existing Python project
- HTMX + server-side rendering = no JavaScript bundling
- Tailwind = responsive, modern UI with utility classes
- Jinja2 = reuse templates between static export and HTTP dashboard

---

## Summary Table: Files & Responsibilities

| File | LOC | Purpose | Key Functions |
|------|-----|---------|----------------|
| `main.py` | ~110 | Orchestrate pipeline | `main()` |
| `config.py` | ~40 | Config + constants | `Config` class |
| `scraper.py` | ~500 | Search + scrape | `search_for_deal_sites()`, `enrich_candidates()`, `scrape_deal_page()` |
| `analyzer.py` | ~75 | LLM analysis | `analyze_site()`, `analyze_sites_batch()` |
| `dashboard_export.py` | ~295 | HTML + JSON export | `export_daily_dashboard()` |
| `logger.py` | ~200 | Structured logging | `StructuredLogger`, `setup_logging()` |
| `sheets.py` | ~70 | Google Sheets integration | `GoogleSheets` class |
| **Total** | **~1290** | — | — |

---

## Quick Reference: Common Tasks

| Task | How | File |
|------|-----|------|
| Add search query | Add string to `Config.SEARCH_QUERIES` | `config.py:25` |
| Change quality threshold | Change `>= 6` to new value | `main.py:49` |
| Modify table columns | Edit CSS + HTML structure | `dashboard_export.py:165–210` |
| Change colors/theme | Update CSS inline styles | `dashboard_export.py:165` |
| Log a new event | Call `logger.info("event_name", message="...", **context)` | Any module |
| Retry API calls | Use `@retry` decorator | `analyzer.py:9` (template) |
| Export to new service (Slack, Discord, etc.) | Create new function in `main.py` after `export_daily_dashboard()` | `main.py:110` |

---

**End of Project Summary**

*For questions or extensions, refer to function signatures, docstrings, and example calls within each module. Good luck!*
