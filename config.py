import os
from dotenv import load_dotenv

load_dotenv(override=True)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


class Config:
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GITHUB_API_KEY = os.getenv("GITHUB_API_KEY")
    GOOGLE_SHEET_ID              = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_SERVICE_ACCOUNT_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
    GOOGLE_SERVICE_ACCOUNT_JSON  = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # Telegram alerts — set both to enable; leave blank to log only
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

    # Affiliate programs — leave blank to disable (links fall back to plain URL)
    AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")

    MEH_DASHBOARD = _env_bool("MEH_DASHBOARD", "1")
    MEH_DASHBOARD_DRY_RUN = _env_bool("MEH_DASHBOARD_DRY_RUN", "0")
    MEH_DASHBOARD_BASE_PATH = os.getenv("MEH_DASHBOARD_BASE_PATH", "").strip()

    MAX_CANDIDATES_PER_RUN = 25
    SEARCH_RESULTS_PER_QUERY = _env_int("SEARCH_RESULTS_PER_QUERY", 12)
    SEARCH_VIBE_THRESHOLD = _env_int("SEARCH_VIBE_THRESHOLD", 4)
    SEARCH_FALLBACK_VIBE_THRESHOLD = _env_int("SEARCH_FALLBACK_VIBE_THRESHOLD", 3)
    SEARCH_MIN_LIVE_CANDIDATES = _env_int("SEARCH_MIN_LIVE_CANDIDATES", 3)
    SEARCH_REJECTION_SAMPLE_LIMIT = _env_int("SEARCH_REJECTION_SAMPLE_LIMIT", 8)

    # ── Curated seed list: known US Meh-style daily deal sites ──────────────
    # These are visited directly every run — no search needed.
    SEED_DEAL_SITES = [
        {"title": "Meh",             "link": "https://meh.com",               "snippet": "One deal a day"},
        {"title": "SideDeal",        "link": "https://sidedeal.com",          "snippet": "Daily deals sister site to Meh"},
        {"title": "That Daily Deal", "link": "https://www.thatdailydeal.com", "snippet": "One deal a day US"},
        {"title": "UntilGone",       "link": "https://www.untilgone.com",     "snippet": "Limited quantity daily deals"},
        {"title": "Tanga",           "link": "https://www.tanga.com",         "snippet": "Deal of the day since 2006"},
        {"title": "1Sale",           "link": "https://www.1sale.com",         "snippet": "Daily deals flash sale"},
        {"title": "Camofire",        "link": "https://www.camofire.com",      "snippet": "Hunting outdoor daily deal one at a time"},
        {"title": "BJJHQ",           "link": "https://www.bjjhq.com",         "snippet": "One BJJ gear deal a day"},
        {"title": "Steep & Cheap",   "link": "https://www.steepandcheap.com", "snippet": "Outdoor gear flash deal daily"},
        {"title": "Drop",            "link": "https://drop.com",              "snippet": "Enthusiast daily deals audio keyboards EDC"},
        {"title": "Touch of Modern", "link": "https://www.touchofmodern.com", "snippet": "Men lifestyle daily flash sale"},
        {"title": "AppSumo",         "link": "https://appsumo.com",           "snippet": "Software deals today only limited"},
        {"title": "Jane",            "link": "https://jane.com",              "snippet": "Women boutique daily deals"},
        {"title": "Last Bottle",     "link": "https://lastbottle.com",        "snippet": "Single wine deal per day until gone"},
        {"title": "Yugster",         "link": "https://www.yugster.com",       "snippet": "Electronics gadget daily deal"},
        {"title": "Woot",            "link": "https://www.woot.com",          "snippet": "Daily deals electronics home tools"},
    ]

    # ── Discovery queries: find NEW sites beyond the seed list ───────────────
    _NEG = "-amazon -ebay -walmart -groupon -slickdeals -coupon -coupons -promo -india -site:.co.za -site:.com.au -site:.co.uk -site:.in"
    SEARCH_QUERIES = [
        f'"deal a day" (hunting OR cycling OR knives OR wine OR "martial arts" OR fishing OR golf OR coffee) shop US {_NEG}',
        f'"deal of the day" independent store US -blog -article {_NEG}',
        f'"one deal a day" shop US -blog -article -reddit {_NEG}',
        f'"today only" "while supplies last" "one item" shop US {_NEG}',
        f'"flash sale" "single item" store US -blog -article {_NEG}',
        f'"new deal every day" shop US -blog -article {_NEG}',
        f'"daily deal" boutique store US "while supplies last" {_NEG}',
    ]
