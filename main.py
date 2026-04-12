import os
import time

from analyzer import analyze_sites_batch
from config import Config
from dashboard_export import export_daily_dashboard, write_project_root_candidate_files
from logger import logger, log_run_summary
from scoring import build_candidate_records
from scraper import enrich_candidates, mark_candidates_seen, search_for_deal_sites
from sheets import append_deals

def main():
    start_time = time.time()
    logger.info("run_started", message="Meh-Scanner starting...")

    # Check config
    if not Config.SERPER_API_KEY or Config.SERPER_API_KEY == "your_serper_key_here":
        logger.error("config_error", message="Please add your Serper API key to .env")
        return

    logger.info("config_loaded", message="All modules loaded successfully!")

    # Phase 3: Scrape and analyze with error handling
    logger.info("search_phase", message="Starting search for deal sites...")
    try:
        sites = search_for_deal_sites()
        if not sites:
            logger.warning("no_sites_found", message="No sites found, exiting")
            return
        sites = enrich_candidates(sites)
    except Exception as e:
        logger.error("search_failed", error=str(e), message=f"Search failed: {str(e)}")
        return

    logger.info("search_phase", message="Starting analysis of deal sites...")
    try:
        analyses = analyze_sites_batch(sites)
        mark_candidates_seen([item["site"] for item in analyses])
    except Exception as e:
        logger.error("analysis_failed", error=str(e), message=f"Analysis failed: {str(e)}")
        # Continue with partial results if available
        analyses = []

    # Filter and format results (same rules as scanner: quality_score >= 6)
    all_candidates = build_candidate_records(analyses)
    deals = []
    for c in all_candidates:
        if not c.get("accepted"):
            continue
        deals.append({
            "site_name": c["site_name"],
            "url": c["url"],
            "rationale": c.get("rationale", ""),
            "niche": c.get("niche", ""),
            "quality_score": c["quality_score"],
            "deal_price": c.get("deal_price", ""),
            "original_price": c.get("original_price", ""),
        })

    if deals:
        logger.info("deals_found", count=len(deals), message=f"Found {len(deals)} potential deals")
        for deal in deals:
            logger.info("deal_detail",
                       message=f"{deal['site_name']} (score {deal['quality_score']})",
                       site_name=deal['site_name'],
                       url=deal['url'],
                       rationale=deal['rationale'][:100],
                       niche=deal['niche'],
                       quality_score=deal['quality_score'])
    else:
        logger.warning("no_deals_passed", message="No deals passed quality threshold")

    # Write vetted deals to Google Sheet (incremental; duplicates are skipped automatically)
    try:
        ok = append_deals(deals)
        if ok:
            logger.info("sheets_write_ok", message="Google Sheet updated with vetted deals")
    except Exception as e:
        logger.error("sheets_write_failed", error=str(e), message=f"Google Sheets write failed: {e}")

    runtime = time.time() - start_time
    try:
        export_daily_dashboard(
            deals,
            candidates_count=len(sites) if sites else 0,
            runtime_seconds=runtime,
            all_candidates=all_candidates,
        )
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
    except Exception as e:
        logger.error("dashboard_export_failed", error=str(e), message=str(e))

    try:
        write_project_root_candidate_files(all_candidates)
    except Exception as e:
        logger.error("candidates_file_write_failed", error=str(e), message=str(e))

    log_run_summary(len(sites) if sites else 0, len(deals), 0, runtime)
    logger.info("run_completed", message="Scan complete!")

if __name__ == "__main__":
    main()
