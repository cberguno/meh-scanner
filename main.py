import os

from config import Config
from logger import logger
from scanner import run_full_scan


def main():
    """
    Thin CLI entry point. Delegates the full pipeline (search → enrich →
    trusted-source bypass → LLM analysis → scoring → market check → alerts →
    dashboard export → Google Sheets append) to scanner.run_full_scan(), which
    is the single source of truth for the scan pipeline and emits the
    scan_summary log line containing the sheets= status.
    """
    logger.info("run_started", "Meh-Scanner starting...")

    if not Config.SERPER_API_KEY or Config.SERPER_API_KEY == "your_serper_key_here":
        logger.error("config_error", "Please add your Serper API key to .env")
        return

    result = run_full_scan()

    if Config.MEH_DASHBOARD and not Config.MEH_DASHBOARD_DRY_RUN:
        gh = os.getenv("GITHUB_REPOSITORY", "").strip()
        if gh and "/" in gh:
            owner, repo = gh.split("/", 1)
            url = f"https://{owner}.github.io/{repo}/"
            logger.info(
                "dashboard_pages_url",
                f"After GitHub Pages deploy, open: {url}",
                pages_url=url,
            )
        else:
            logger.info(
                "dashboard_open_local",
                "Dashboard written under public/ — open public/index.html in a browser",
            )

    if not result.get("success"):
        logger.error("run_failed", result.get("error", "unknown error"))


if __name__ == "__main__":
    main()
