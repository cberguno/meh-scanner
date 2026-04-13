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

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
    AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")

    MEH_DASHBOARD = _env_bool("MEH_DASHBOARD", "1")
    MEH_DASHBOARD_DRY_RUN = _env_bool("MEH_DASHBOARD_DRY_RUN", "0")
    MARKET_CHECK_ENABLED = _env_bool("MARKET_CHECK_ENABLED", "1")
    MEH_DASHBOARD_BASE_PATH = os.getenv("MEH_DASHBOARD_BASE_PATH", "").strip()

    MAX_CANDIDATES_PER_RUN = 25
    SEARCH_RESULTS_PER_QUERY = _env_int("SEARCH_RESULTS_PER_QUERY", 12)
    SEARCH_VIBE_THRESHOLD = _env_int("SEARCH_VIBE_THRESHOLD", 4)
    SEARCH_FALLBACK_VIBE_THRESHOLD = _env_int("SEARCH_FALLBACK_VIBE_THRESHOLD", 3)
    SEARCH_MIN_LIVE_CANDIDATES = _env_int("SEARCH_MIN_LIVE_CANDIDATES", 3)
    SEARCH_REJECTION_SAMPLE_LIMIT = _env_int("SEARCH_REJECTION_SAMPLE_LIMIT", 8)

    # ── Curated seed list of known US Meh-style daily deal sites ────────────
    # This is the PRIMARY source. Google search cannot reliably find these sites.
    # Add new sites here after manual research.
    SEED_DEAL_SITES = [
        # ── Core one-deal-a-day sites ────────────────────────────────────────
        {"title": "Meh",             "link": "https://meh.com",               "snippet": "One deal a day, mediocre products"},
        {"title": "SideDeal",        "link": "https://sidedeal.com",          "snippet": "Meh sister site, daily deals"},
        {"title": "Woot",            "link": "https://www.woot.com",          "snippet": "Daily deals electronics home tools"},
        {"title": "That Daily Deal", "link": "https://www.thatdailydeal.com", "snippet": "One deal a day US"},
        {"title": "UntilGone",       "link": "https://www.untilgone.com",     "snippet": "Limited quantity daily deals"},
        {"title": "Tanga",           "link": "https://www.tanga.com",         "snippet": "Deal of the day since 2006"},
        {"title": "1Sale",           "link": "https://www.1sale.com",         "snippet": "Daily deals flash sale"},
        {"title": "13 Deals",        "link": "https://www.13deals.com",       "snippet": "Daily deals flash sale"},
        {"title": "Yugster",         "link": "https://www.yugster.com",       "snippet": "Electronics gadget daily deal"},
        # ── Niche outdoor / hunting / sports ────────────────────────────────
        {"title": "Camofire",        "link": "https://www.camofire.com",      "snippet": "Hunting outdoor daily deal one at a time"},
        {"title": "BJJHQ",           "link": "https://www.bjjhq.com",         "snippet": "One BJJ gear deal a day"},
        {"title": "Steep & Cheap",   "link": "https://www.steepandcheap.com", "snippet": "Outdoor gear flash deal daily"},
        {"title": "Chainlove",       "link": "https://www.chainlove.com",     "snippet": "Cycling gear daily deal"},
        # ── Enthusiast / lifestyle ───────────────────────────────────────────
        {"title": "Drop",            "link": "https://drop.com",              "snippet": "Enthusiast daily deals audio keyboards EDC"},
        {"title": "Touch of Modern", "link": "https://www.touchofmodern.com", "snippet": "Men lifestyle daily flash sale"},
        {"title": "Jane",            "link": "https://jane.com",              "snippet": "Women boutique daily deals"},
        # ── Wine / food ──────────────────────────────────────────────────────
        {"title": "Last Bottle",     "link": "https://lastbottle.com",        "snippet": "Single wine deal per day until gone"},
        {"title": "Wine Insiders",   "link": "https://www.wineinsiders.com",  "snippet": "Flash wine deals US"},
        # ── Software / digital ───────────────────────────────────────────────
        {"title": "AppSumo",         "link": "https://appsumo.com",           "snippet": "Software deals today only limited"},
    ]

    # ── Discovery queries ────────────────────────────────────────────────────
    # Search yields very low signal — niche verticals are the only productive path.
    # Kept minimal to save Serper credits; seed list does the primary work.
    _NEG = "-amazon -ebay -walmart -groupon -slickdeals -coupon -coupons -promo -india -site:.co.za -site:.com.au -site:.co.uk -site:.in"
    SEARCH_QUERIES = [
        # Niche verticals where indie deal sites still survive
        f'"deal a day" (hunting OR fishing OR "martial arts" OR cycling OR knives OR coffee OR wine OR whiskey OR cigars OR golf OR tools OR camping OR archery OR shooting) shop {_NEG}',
        # Countdown mechanic — only used on real deal sites
        f'"expires at midnight" OR "expires tonight" "add to cart" shop {_NEG}',
        # Explicit daily-deal business model
        f'"new deal every day" OR "one new deal daily" shop buy {_NEG}',
    ]
