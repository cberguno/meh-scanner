"""
AI-powered discovery of new daily-deal sites via Claude Haiku.

Calls the Anthropic API to brainstorm niche one-deal-a-day sites,
validates them, and persists discoveries to discovered_sites.json.
"""
import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from anthropic import Anthropic

from config import Config
from logger import logger
from scraper import _is_blocked_domain, _extract_registrable_domain

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


def _head_check(url: str) -> tuple[bool, str]:
    """HEAD check that returns (reachable, final_domain_after_redirects)."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
        final_domain = _extract_registrable_domain(resp.url)
        return resp.status_code < 400, final_domain
    except Exception:
        return False, ""


def _seed_domains() -> set[str]:
    """Collect registrable domains from all seed sites."""
    domains = set()
    for seed in Config.SEED_DEAL_SITES:
        domains.add(_extract_registrable_domain(seed["link"]))
    return domains


def _score_site(client: Anthropic, site: dict) -> int | None:
    """Call Claude Haiku with the analyzer prompt to score a discovered site.

    Returns quality_score (int) or None on failure.
    """
    ctx = f"""Search result title: {site.get('title', '')}
URL: {site.get('link', '')}
Snippet: {site.get('snippet', '')}

From live page scrape:
- Deal title (on-page): (none)
- Price signal: (none)
- Promo / body excerpt: {site.get('snippet', '')}
- Meh-vibe keyword hits: (none)
- Screenshot (local path for your context only): (none)
- Scrape method: head_check"""

    prompt = f"""You are vetting candidates for someone hunting indie "one deal a day" / Meh-style sites.

{ctx}

CRITICAL DISQUALIFIERS — score 0 immediately if ANY apply:
- Article, blog post, or listicle ABOUT deal sites — not itself a deal site
- News outlet, tech blog, or review site (any major media domain)
- Restaurant, food delivery, bar, or café running a daily food special
- Florist, gift shop, or local service business with a "deal of the day"
- Cannabis dispensary or local retail store
- Affiliate blog promoting another store's deals
- Community forum, social media post, or user discussion thread
- Real estate, financial services, or B2B company
- Non-US site (South Africa, Portugal, Indonesia, Australia, UK, India, etc.)
- Large marketplace (Amazon, eBay, Walmart, etc.)
- Coupon or promo-code aggregator
- Large general retailer with a deals page (not a dedicated daily-deal site)

ONLY score ≥6 if: a US-based, independently operated website whose PRIMARY business model is selling ONE discounted product per day directly to consumers.

Score rubric:
  0-2  Not a deal site at all
  3-4  Deal-adjacent but wrong format
  5-6  Has deals but lacks focus or clear single-item format
  7-8  Solid single-item daily deal site with some personality
  9-10 Textbook Meh clone

Be concise in rationale (one sentence max). Return JSON only:
{{"rationale": "...", "quality_score": <int 0-10>, "niche": "..."}}"""

    try:
        resp = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=220,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        data = json.loads(raw)
        return int(data.get("quality_score", 0))
    except Exception as exc:
        logger.warning("discovery_score_failed", error=str(exc),
                       message=f"Scoring failed for {site.get('link', '?')}: {exc}")
        return None


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
    known_domains = _seed_domains()
    # Also include domains from previously discovered sites
    for d in _load_discovered():
        known_domains.add(_extract_registrable_domain(d["link"]))
    verified: list[dict] = []

    for s in high_conf:
        url = (s.get("url") or "").rstrip("/").lower()
        if not url:
            continue
        # Skip blocked domains
        if _is_blocked_domain(url):
            logger.info("discovery_blocked", message=f"Skipping blocked domain: {url}", url=url)
            continue
        # Skip URL duplicates
        if url in existing:
            logger.info("discovery_duplicate", message=f"Already known: {url}", url=url)
            continue
        # Skip if domain already in seeds
        domain = _extract_registrable_domain(url)
        if domain in known_domains:
            logger.info("discovery_domain_dup",
                        message=f"Domain already known: {domain} ({url})", url=url, domain=domain)
            continue
        # Verify reachable and check redirect destination for domain aliases
        reachable, final_domain = _head_check(s["url"])
        if not reachable:
            logger.info("discovery_unreachable", message=f"Not reachable: {s['url']}", url=s["url"])
            continue
        if final_domain and final_domain != domain and final_domain in known_domains:
            logger.info("discovery_redirect_dup",
                        message=f"{url} redirects to known domain {final_domain}",
                        url=url, final_domain=final_domain)
            continue

        site = {
            "title": s.get("name", ""),
            "link": s["url"],
            "snippet": s.get("snippet", ""),
            "niche": s.get("niche", ""),
            "confidence": s.get("confidence", 0),
            "discovery_source": "ai",
        }

        # Score with Claude to filter out large retailers / non-deal sites
        score = _score_site(client, site)
        if score is None:
            logger.info("discovery_score_skip",
                        message=f"Could not score {site['title']} — skipping", url=url)
            continue
        if score < 6:
            logger.info("discovery_low_score",
                        message=f"{site['title']} scored {score} (< 6) — rejected",
                        url=url, score=score)
            continue

        site["quality_score"] = score
        verified.append(site)
        existing.add(url)
        known_domains.add(domain)
        if final_domain:
            known_domains.add(final_domain)
        logger.info("discovery_new",
                     message=f"New site discovered: {site['title']} ({site['link']}) score={score}",
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
