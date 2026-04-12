import argparse
import os
import time

from analyzer import analyze_sites_batch
from config import Config
from dashboard_export import export_daily_dashboard, write_project_root_candidate_files
from logger import logger, log_run_summary
from scoring import build_candidate_records
from scraper import enrich_candidates, mark_candidates_seen, search_for_deal_sites

def main():
    parser = argparse.ArgumentParser(description="Meh-Scanner: daily deal site discovery")
    parser.add_argument("--discover", action="store_true",
                        help="Run AI-powered discovery of new deal sites before scanning")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("run_started", message="Meh-Scanner starting...")

    # Check config
    if not Config.SERPER_API_KEY or Config.SERPER_API_KEY == "your_serper_key_here":
        logger.error("config_error", message="Please add your Serper API key to .env")
        return

    logger.info("config_loaded", message="All modules loaded successfully!")

    # ── AI Discovery (optional) ──────────────────────────────────────────
    if args.discover:
        try:
            from ai_discovery import discover_new_sites
            new_sites = discover_new_sites()
            if new_sites:
                logger.info("discovery_complete",
                            message=f"AI discovery found {len(new_sites)} new sites",
                            count=len(new_sites))
        except Exception as e:
            logger.error("discovery_failed", error=str(e),
                         message=f"AI discovery failed: {e}")

    # ── Load previously discovered sites into candidate pool ─────────────
    discovered_candidates = []
    try:
        from ai_discovery import get_discovered_as_candidates
        discovered_candidates = get_discovered_as_candidates()
        if discovered_candidates:
            logger.info("discovery_loaded",
                        message=f"Loaded {len(discovered_candidates)} previously discovered sites",
                        count=len(discovered_candidates))
    except Exception:
        pass

    # Phase 3: Scrape and analyze with error handling
    logger.info("search_phase", message="Starting search for deal sites...")
    try:
        sites = search_for_deal_sites()
        # Merge discovered sites (deduplicate by link)
        if discovered_candidates:
            existing = {s["link"].rstrip("/").lower() for s in sites}
            for dc in discovered_candidates:
                if dc["link"].rstrip("/").lower() not in existing:
                    sites.append(dc)
                    existing.add(dc["link"].rstrip("/").lower())
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

        # Write accepted deals to Google Sheet
        try:
            from sheets import append_deals
            sheets_ok = append_deals(deals)
            if sheets_ok:
                logger.info("sheets_done", f"Wrote {len(deals)} deals to Google Sheet")
            else:
                logger.warning("sheets_skipped", "Google Sheet write returned False (check config)")
        except (ImportError, Exception, BaseException) as e:
            logger.error("sheets_failed", error=str(e), message=f"Sheet write failed (will retry next run): {e}")
    else:
        logger.warning("no_deals_passed", message="No deals passed quality threshold")

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
