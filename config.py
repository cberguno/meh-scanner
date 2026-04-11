import os
from dotenv import load_dotenv

load_dotenv(override=True)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


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
    # Path-only base for GitHub project Pages, e.g. /my-repo/ → https://user.github.io/my-repo/
    MEH_DASHBOARD_BASE_PATH = os.getenv("MEH_DASHBOARD_BASE_PATH", "").strip()

    # Safety limits to keep costs low
    MAX_CANDIDATES_PER_RUN = 25
    SEARCH_QUERIES = [
        # Core strong negations (aggregators & big marketplaces)
        '"one deal a day" OR "single daily deal" -groupon -slickdeals -woot -amazon -ebay -aliexpress -temu -marketplace',
        '"meh style" OR "meh.com style" daily deal -groupon -slickdeals -woot',
        '"one sale a day" OR "one product a day" shop OR website -groupon -slickdeals -woot',
        '"daily flash sale" "one product" OR "single product" -groupon -slickdeals -woot',
        
        # Witty / cynical / fun vibe indicators (Meh DNA)
        '"daily deal" (witty OR cynical OR sarcastic OR humorous) -groupon -slickdeals',
        '"one thing" daily deal OR sale -groupon -slickdeals -woot',
        
        # Broader discovery (run less frequently or with lower priority)
        'indie "deal of the day" OR "daily single deal" site -aggregator',
    ]
