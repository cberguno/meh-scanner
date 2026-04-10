# Meh-Scanner - One Sale a Day Sites

## Goal
Daily tool that:
- Searches the web for new "one sale a day" / Meh-style websites
- Visits each promising site
- Analyzes current deal, pricing, quality, brand, niche
- Writes clear rationale why it's good
- Auto-creates and appends to a Google Sheet
- Keeps runs cheap and reliable

## Tech Stack (pinned)
- Python 3.12
- Playwright (headless browser)
- Anthropic SDK (for rationale)
- Serper.dev for search
- Google Sheets API

## Project Structure
```
meh-scanner/
├── CLAUDE.md
├── config.py
├── main.py
├── scraper.py
├── analyzer.py
├── sheets.py
├── requirements.txt
├── .env.example
├── .gitignore
└── logs/
```

## Exact Commands
- Install deps: `py -m pip install -r requirements.txt`
- Install browsers: `playwright install`
- Run: `py main.py`

## Environment Variables
See .env.example

## Rules
- Skip preambles. Output only code or requested answer.
- Edit files surgically, never rewrite entire file.
- Limit to max 25 candidates per daily run.
- Test each module before moving on.
- Use Gemini subagent for any analysis of multiple files.
