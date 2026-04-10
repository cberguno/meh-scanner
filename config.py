import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
    GOOGLE_SERVICE_ACCOUNT_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")

    # Safety limits to keep costs low
    MAX_CANDIDATES_PER_RUN = 25
    SEARCH_QUERIES = [
        "one deal a day site -meh",
        "meh style daily deal website",
        "single product daily sale shop",
        "one sale a day niche store",
        "daily flash sale one product"
    ]
