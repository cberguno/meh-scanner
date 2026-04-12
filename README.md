# Meh-Scanner 🛍️

**Autonomous daily deal discovery for indie "one sale a day" / Meh-style websites.**

Meh-Scanner searches the web each day for quirky, personality-driven deal sites, scrapes their current offers, scores quality with AI, and publishes the best finds to an interactive dashboard and a shared Google Spreadsheet.

---

## Live Outputs

| Output | Link |
|--------|------|
| 📊 **Live Dashboard** | [GitHub Pages →](https://cberguno.github.io/meh-scanner/) |
| 📋 **Vetted Products Spreadsheet** | *(see [Google Spreadsheet](#google-spreadsheet) section below)* |

---

## Google Spreadsheet

All vetted product data discovered by Meh-Scanner is automatically exported to a shared Google Spreadsheet after each daily run.

> **🔗 Spreadsheet Link:**
> <!-- TODO (admin): Replace this line with the actual Google Spreadsheet URL once export is configured.
>      Example: https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit?usp=sharing -->
> _Link not yet published — see admin instructions below._

### What's in the spreadsheet?

Each row represents one vetted deal that scored ≥ 6 out of 10, and contains:

| Column | Description |
|--------|-------------|
| **Site** | Name of the deal site |
| **URL** | Direct link to the product/deal page |
| **Niche** | Category (e.g., Electronics, Home Goods) |
| **Score** | AI quality score (0–10) |
| **Price** | Current deal price |
| **Was** | Original / MSRP price |
| **Est. ROI %** | Estimated discount percentage |
| **Rationale** | AI-generated explanation of why the deal is good |
| **Scanned At** | UTC timestamp of when the deal was discovered |

New rows are appended automatically after each run; duplicates (by URL) are skipped.

### Admin: How to publish the spreadsheet link

Once `GOOGLE_SHEET_ID` is configured (see [Setup](#setup)), the spreadsheet is populated automatically. To share access and document the link:

1. Open [Google Sheets](https://sheets.google.com) and find the spreadsheet whose ID matches `GOOGLE_SHEET_ID` in your `.env` file.
   - The direct URL is `https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit`
2. Click **Share → Anyone with the link → Viewer** to make it publicly readable (optional, recommended for teams).
3. Copy the share URL and replace the placeholder in this README:

```markdown
> **🔗 Spreadsheet Link:**
> [View all vetted deals →](https://docs.google.com/spreadsheets/d/<YOUR_SHEET_ID>/edit?usp=sharing)
```

4. Commit the updated README so the link is discoverable for all contributors.

> **Tip:** If you haven't created the spreadsheet yet, run `python setup_sheets.py` — it creates the sheet and prints the URL automatically.

---

## Setup

### Prerequisites

- Python 3.12
- A [Serper.dev](https://serper.dev) API key
- An [Anthropic](https://www.anthropic.com) API key
- (Optional) A Google service account for Sheets export

### Install

```bash
pip install -r requirements.txt
playwright install
```

### Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Purpose |
|----------|----------|---------|
| `SERPER_API_KEY` | ✅ | Serper.dev web search |
| `ANTHROPIC_API_KEY` | ✅ | Claude AI analysis |
| `GOOGLE_SHEET_ID` | ❌ | Google Sheet to append deals to |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ❌ | Service account credentials (JSON string or base64) |

### Google Sheets (optional but recommended)

1. Create a Google Cloud project and enable the **Google Sheets API**.
2. Create a **service account**, download the JSON key.
3. Set `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` (paste the JSON directly, or base64-encode it).
4. Run the one-time setup to create the spreadsheet:

```bash
python setup_sheets.py
```

5. Copy the printed spreadsheet URL and add it to this README (see [Admin instructions](#admin-how-to-publish-the-spreadsheet-link) above).

### Run

```bash
python main.py
```

---

## Project Structure

```
meh-scanner/
├── main.py                # Entry point — orchestrates the full pipeline
├── config.py              # Env vars, search queries, safety limits
├── scraper.py             # Web search + Playwright/HTTP scraping
├── analyzer.py            # Claude AI analysis (parallel batch)
├── scoring.py             # Vibe / quality scoring helpers
├── sheets.py              # Google Sheets integration
├── dashboard_export.py    # Generates public/index.html + public/latest.json
├── logger.py              # Structured logging (console + JSON file)
├── setup_sheets.py        # One-time Sheets setup script
├── requirements.txt
├── .env.example
├── public/                # Generated dashboard (deployed to GitHub Pages)
└── logs/                  # JSON log files (daily rotation)
```

---

## Documentation

- [GitHub Pages Setup](GITHUB_PAGES_SETUP.md) — deploy the dashboard
- [Deploy Checklist](DEPLOY_CHECKLIST.md) — pre-deployment verification
- [Project Summary](PROJECT_SUMMARY.md) — architecture deep-dive

---

## License

MIT
