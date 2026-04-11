import os

from dotenv import load_dotenv

load_dotenv(override=True)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class TravelConfig:
    TRAVEL_PROVIDER = os.getenv("TRAVEL_PROVIDER", "mock").strip().lower()
    MAX_FLIGHT_RESULTS = int(os.getenv("MAX_FLIGHT_RESULTS", "5"))
    MAX_HOTEL_RESULTS = int(os.getenv("MAX_HOTEL_RESULTS", "5"))
    TRAVEL_CURRENCY = os.getenv("TRAVEL_CURRENCY", "USD").strip().upper()

    # Future provider keys
    AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY", "")
    SKYSCANNER_API_KEY = os.getenv("SKYSCANNER_API_KEY", "")
