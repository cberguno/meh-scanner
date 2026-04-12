"""
AI-powered discovery of new daily-deal sites via Claude Haiku.

Calls the Anthropic API to brainstorm niche one-deal-a-day sites,
validates them, and persists discoveries to discovered_sites.json.
"""
import json
from pathlib import Path

import requests
from anthropic import Anthropic

from config import Config
from logger import logger
from scraper import _is_blocked_domain

DISCOVERED_PATH = Path("discovered_sites.json")

DISCOVERY_PROMPT = """\
You are a deal-site researcher. Your job is to find US-based indie websites whose PRIMARY business model is selling ONE discounted product per day directly to consumers (like meh.com, woot.com, sidedeal.com).

DO NOT suggest: coupon aggregators, cashback sites, deal communities, major marketplaces (Amazon, eBay, Walmart), Groupon-style local deal sites, browser extensions, or general retailers with a deals page.

ONLY suggest sites where the entire business IS the daily deal.

Think about niche verticals: hunting, fishing, cycling, BJJ, wine, cigars, coffee, knives, EDC, golf, tools, camping, audio, keyboards, watches, outdoor gear, pet supplies, kids toys, board games, craft supplies, fishing tackle, running shoes, supplements, home brewing, woodworking.

Return ONLY a JSON array of objects with these fields:
- name: site name
- url: full URL
- snippet: one-line description
- niche: what vertical it serves
- confidence: 1-10 how confident you are this is a real active daily-deal site

Return 10-15 suggestions. JSON only, no markdown fences."""


def _load_discovered() -> list[dict]:
    """Load previously discovered sites from disk."""
    if not DISCOVERED_PATH.exists():
        return []
    try:
        data = json.loads(DISCOVERED_PATH.read_text())
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("discovery_load_failed", error=str(exc),
                       message=f"Could not load {DISCOVERED_PATH}: {exc}")
    return []


def _save_discovered(sites: list[dict]) -> None:
    """Persist discovered sites to disk."""
    DISCOVERED_PATH.write_text(json.dumps(sites, indent=2))


def _existing_urls() -> set[str]:
    """Collect URLs already in seeds so we skip duplicates."""
    urls = set()
    for seed in Config.SEED_DEAL_SITES:
        urls.add(seed["link"].rstrip("/").lower())
    for site in _load_discovered():
        urls.add(site["link"].rstrip("/").lower())
    return urls


def _is_reachable(url: str) -> bool:
    """Quick HEAD check to verify a URL is live."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
        return resp.status_code < 400
    except Exception:
        return False


def discover_new_sites() -> list[dict]:
    """Call Claude Haiku to brainstorm new daily-deal sites, validate, and return."""
    if not Config.ANTHROPIC_API_KEY:
        logger.warning("discovery_skip", "No ANTHROPIC_API_KEY — skipping AI discovery")
        return []

    logger.info("discovery_start", message="Asking Claude for new daily-deal site suggestions...")

    client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1000,
            temperature=0.3,
            messages=[{"role": "user", "content": DISCOVERY_PROMPT}],
        )
    except Exception as exc:
        logger.error("discovery_api_failed", error=str(exc),
                     message=f"Anthropic API call failed: {exc}")
        return []

    raw = resp.content[0].text.strip()

    # Strip markdown fences if present despite instructions
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()

    try:
        suggestions = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("discovery_parse_failed", error=str(exc),
                     message=f"Failed to parse Claude response as JSON: {exc}")
        return []

    if not isinstance(suggestions, list):
        logger.error("discovery_bad_format", message="Claude returned non-list JSON")
        return []

    # Filter by confidence
    high_conf = [s for s in suggestions if s.get("confidence", 0) >= 7]
    logger.info("discovery_filtered",
                message=f"{len(high_conf)}/{len(suggestions)} suggestions have confidence >= 7",
                total=len(suggestions), high_conf=len(high_conf))

    existing = _existing_urls()
    verified: list[dict] = []

    for s in high_conf:
        url = (s.get("url") or "").rstrip("/").lower()
        if not url:
            continue
        # Skip blocked domains
        if _is_blocked_domain(url):
            logger.info("discovery_blocked", message=f"Skipping blocked domain: {url}", url=url)
            continue
        # Skip duplicates
        if url in existing:
            logger.info("discovery_duplicate", message=f"Already known: {url}", url=url)
            continue
        # Verify reachable
        if not _is_reachable(s["url"]):
            logger.info("discovery_unreachable", message=f"Not reachable: {s['url']}", url=s["url"])
            continue

        site = {
            "title": s.get("name", ""),
            "link": s["url"],
            "snippet": s.get("snippet", ""),
            "niche": s.get("niche", ""),
            "confidence": s.get("confidence", 0),
            "discovery_source": "ai",
        }
        verified.append(site)
        existing.add(url)
        logger.info("discovery_new",
                     message=f"New site discovered: {site['title']} ({site['link']})",
                     **site)

    # Merge with previously discovered and save
    if verified:
        all_discovered = _load_discovered()
        known_links = {s["link"].rstrip("/").lower() for s in all_discovered}
        for site in verified:
            if site["link"].rstrip("/").lower() not in known_links:
                all_discovered.append(site)
        _save_discovered(all_discovered)
        logger.info("discovery_saved",
                     message=f"Saved {len(all_discovered)} total discovered sites",
                     new=len(verified), total=len(all_discovered))

    return verified


def get_discovered_as_candidates() -> list[dict]:
    """Load all previously discovered sites formatted as search candidates."""
    candidates = []
    for site in _load_discovered():
        candidates.append({
            "title": site.get("title", ""),
            "link": site["link"],
            "snippet": site.get("snippet", ""),
            "vibe_score": 7,
            "discovery_source": "ai",
            "search_query": "",
        })
    return candidates
