import hashlib
import json
import random
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from db import init_db, is_site_seen, mark_site_seen, get_source_status, _extract_domain as _db_extract_domain, _normalize_url_for_seen as normalize_url
from logger import logger, log_search_start, log_search_complete, log_site_scraped

SCREENSHOTS_DIR = Path("logs") / "screenshots"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
]

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

MEH_SIGNAL_KEYWORDS = (
    "one deal",
    "one sale",
    "daily deal",
    "today only",
    "midnight",
    "limited time",
    "meh",
    "sarcastic",
    "witty",
    "snark",
    "no thanks",
    "pass",
    "woot",
    "flash sale",
    "single item",
)

SERPER_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_LAST_SEARCH_DIAGNOSTICS: dict = {}

def score_meh_vibe(title, snippet):
    """Cheap heuristic scoring for 'Meh vibe' before Playwright visit"""
    score = 0
    text = f"{title} {snippet}".lower()
    
    # Positive indicators (Meh-like)
    positive_keywords = [
        'deal', 'sale', 'daily', 'one', 'single', 'limited', 'flash',
        'exclusive', 'offer', 'discount', 'bargain', 'steal', 'score',
        'witty', 'fun', 'cynical', 'sarcastic', 'humor', 'joke',
        'drops',  # limited-release deal mechanic used by drop.com and similar
    ]
    
    # Negative indicators (aggregators/marketplaces)
    # Note: 'woot' removed — woot.com is a legitimate meh-style deal site.
    negative_keywords = [
        'groupon', 'slickdeals', 'amazon', 'ebay', 'aliexpress',
        'temu', 'walmart', 'target', 'best buy', 'coupons', 'thousands',
        'million', 'marketplace', 'storefront', 'shopify', 'etsy',
        # Coupon/promo-code aggregators (e.g. 1sale.com) use these exact phrases
        'coupon codes', 'promo codes',
        # Geographic / catalog false positives
        'india', 'flipkart', 'myntra', 'snapdeal',
        # Multi-product catalog indicators (not one-item-per-day)
        'all deals', 'hundreds of deals', 'thousands of deals',
        'retailer of', 'supplier of', 'manufacturer of', 'wholesaler',
        'get upto', 'get up to',
        # Article/listicle indicators — sites writing ABOUT deal sites, not being one
        'best deal sites', 'top deal sites', 'sites that', 'these sites',
        'best websites', 'top websites', 'list of sites', 'deal sites that',
        'daily deal sites', 'one deal a day sites',
    ]
    
    for keyword in positive_keywords:
        if keyword in text:
            score += 1
    
    for keyword in negative_keywords:
        if keyword in text:
            score -= 2
    
    # Increased bonus for domain patterns
    if re.search(r'(deal|sale|meh|daily|steal|score)', title.lower()):
        score += 2
    
    # Small penalty for generic Shopify/Etsy-style domains
    if re.search(r'(myshopify\.com|etsy\.com|shopify\.com)', text):
        score -= 1
    
    return max(0, min(10, score))  # Clamp to 0-10

# Domains that consistently return false positives: social platforms, review
# aggregators, tutorial blogs, plugin directories, and messaging apps.
# These are never direct deal-site sources regardless of their snippet text.
# Keep this list narrow — block only clearly non-deal domain families.
_BLOCKED_DOMAINS = frozenset({
    # Social / content platforms
    "reddit.com", "facebook.com", "instagram.com",
    "tiktok.com", "shop.tiktok.com", "soundcloud.com",
    "youtube.com", "twitter.com", "x.com", "linkedin.com",
    "pinterest.com", "wa.me", "whatsapp.com",
    # Review / trust aggregators
    "trustpilot.com", "hellopeter.com", "yelp.com", "g2.com",
    "capterra.com", "sitejabber.com",
    # Plugin / tutorial / blog directories
    "wordpress.org", "medium.com", "substack.com",
    "dev.to", "zhihu.com",
    # Recurring false positives from search results
    "evreward.com",                  # coupon aggregator
    "netcorecloud.com",              # email-marketing SaaS — surfaces via "One Deal A Day" case study title
    "ecommercetrainingacademy.com",  # e-commerce blog, not a deal site
    "play.google.com",               # app store listings, not deal sites
    # Confirmed garbage from live scans
    "indiadesire.com",               # Indian affiliate blog, not a deal site
    "exportersindia.com",            # Indian B2B marketplace, completely irrelevant
    "dealsmagnet.com",               # generic Indian deal aggregator
    "couponorg.com",                 # coupon/promo code aggregator
    "myntra.com",                    # large Indian fashion marketplace
    "flipkart.com",                  # large Indian e-commerce marketplace
    "amazon.in",                     # Indian Amazon
    "snapdeal.com",                  # Indian e-commerce marketplace
    # Tech/news article sites — write about deal sites but are not deal sites
    "makeuseof.com",
    "askbobrankin.com",
    "nytimes.com",
    "pcmag.com",
    "cnet.com",
    "techradar.com",
    "tomsguide.com",
    "theverge.com",
    "lifehacker.com",
    "buzzfeed.com",
    "businessinsider.com",
    "huffpost.com",
    "forbes.com",
    "wsj.com",
    "techcrunch.com",
    "wired.com",
    "pcworld.com",
    "digitaltrends.com",
    "slashgear.com",
    "9to5mac.com",
    "9to5google.com",
    "androidpolice.com",
    "gamesindustry.biz",
})


def _is_blocked_domain(url: str) -> bool:
    """Return True if the URL's registrable domain is in _BLOCKED_DOMAINS."""
    try:
        from urllib.parse import urlparse
        # urlparse requires a scheme to populate netloc correctly
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        # Match exact domain or any subdomain (e.g. shop.tiktok.com → tiktok.com)
        return host in _BLOCKED_DOMAINS or any(
            host.endswith("." + d) for d in _BLOCKED_DOMAINS
        )
    except Exception:
        return False


def _normalize_force_domains(force_domains) -> frozenset[str]:
    """Normalize manual overrides so callers can pass bare domains or full URLs."""
    normalized = set()
    for value in force_domains or ():
        raw = str(value).strip().lower()
        if not raw:
            continue
        normalized.add(_db_extract_domain(raw))
    return frozenset(normalized)


def get_last_search_diagnostics() -> dict:
    """Return the most recent search diagnostics snapshot."""
    diagnostics = dict(_LAST_SEARCH_DIAGNOSTICS)
    diagnostics["drop_reasons"] = dict(diagnostics.get("drop_reasons") or {})
    diagnostics["query_candidates"] = dict(diagnostics.get("query_candidates") or {})
    diagnostics["query_borderline_candidates"] = dict(diagnostics.get("query_borderline_candidates") or {})
    diagnostics["rejection_samples"] = list(diagnostics.get("rejection_samples") or [])
    return diagnostics


def mark_candidates_seen(sites: list[dict]) -> int:
    """Mark successfully processed candidate URLs as seen after downstream work completes."""
    marked = 0
    seen = set()
    for site in sites:
        url = site.get("link") or site.get("url") or ""
        if not url:
            continue
        normalized = normalize_url(url)
        if normalized in seen:
            continue
        mark_site_seen(url)
        seen.add(normalized)
        marked += 1

    logger.info(
        "seen_candidates_marked",
        f"Marked {marked} processed candidates as seen",
        marked=marked,
    )
    return marked


def _append_rejection_sample(
    samples: list[dict],
    sample_keys: set[tuple[str, str]],
    *,
    reason: str,
    query: str,
    result: dict,
    limit: int,
    vibe_score: int | None = None,
) -> None:
    """Keep a small sample of filtered search results for debugging and tuning."""
    if limit <= 0 or len(samples) >= limit:
        return

    title = str(result.get("title") or "").strip()
    url = str(result.get("link") or "").strip()
    key = (reason, normalize_url(url) or title[:120])
    if key in sample_keys:
        return

    sample = {
        "reason": reason,
        "query": query,
        "title": title[:140],
        "url": url,
    }
    if vibe_score is not None:
        sample["vibe_score"] = vibe_score

    samples.append(sample)
    sample_keys.add(key)


def search_for_deal_sites(force_domains: frozenset = frozenset()):
    """Search for potential one-sale-a-day sites using Serper + curated seed list."""
    global _LAST_SEARCH_DIAGNOSTICS
    force_domains = _normalize_force_domains(force_domains)
    init_db()
    log_search_start(len(Config.SEARCH_QUERIES))

    strict_vibe_threshold = max(0, Config.SEARCH_VIBE_THRESHOLD)
    fallback_vibe_threshold = min(strict_vibe_threshold, max(0, Config.SEARCH_FALLBACK_VIBE_THRESHOLD))
    min_live_candidates = max(0, Config.SEARCH_MIN_LIVE_CANDIDATES)
    sample_limit = max(0, Config.SEARCH_REJECTION_SAMPLE_LIMIT)

    drop_reasons: Counter[str] = Counter()
    query_candidates: dict[str, int] = {}
    query_borderline_candidates: dict[str, int] = {}
    rejection_samples: list[dict] = []
    rejection_sample_keys: set[tuple[str, str]] = set()
    diagnostics = {
        "queries_total": len(Config.SEARCH_QUERIES),
        "queries_succeeded": 0,
        "queries_failed": 0,
        "raw_search_results": 0,
        "forced_domains": sorted(force_domains),
        "strict_vibe_threshold": strict_vibe_threshold,
        "fallback_vibe_threshold": fallback_vibe_threshold,
        "search_results_per_query": Config.SEARCH_RESULTS_PER_QUERY,
    }
    
    headers = {
        'X-API-KEY': Config.SERPER_API_KEY,
        'Content-Type': 'application/json'
    }
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
        reraise=True
    )
    def search_query(query):
        query_drop_reasons: Counter[str] = Counter()
        query_borderline_results = []
        query_samples: list[dict] = []
        query_sample_keys: set[tuple[str, str]] = set()
        payload = {"q": query, "num": Config.SEARCH_RESULTS_PER_QUERY, "gl": "us", "hl": "en"}
        try:
            response = requests.post("https://google.serper.dev/search", headers=headers, json=payload, timeout=10)
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "serper_request_retrying",
                query=query,
                error=str(exc),
                message=f"Retrying Serper query '{query}' after request failure: {exc}",
            )
            raise

        if response.status_code in SERPER_RETRYABLE_STATUS_CODES:
            error = requests.exceptions.HTTPError(
                f"Serper transient error for '{query}': {response.status_code}",
                response=response,
            )
            logger.warning(
                "serper_transient_error",
                query=query,
                status_code=response.status_code,
                message=f"Retrying Serper query '{query}' after HTTP {response.status_code}",
            )
            raise error

        if response.status_code != 200:
            logger.error(
                "serper_error",
                query=query,
                status_code=response.status_code,
                message=f"Serper error for '{query}': {response.status_code}",
            )
            query_drop_reasons[f"http_{response.status_code}"] += 1
            return {
                "query": query,
                "ok": False,
                "raw_results": 0,
                "results": [],
                "borderline_results": [],
                "drop_reasons": query_drop_reasons,
                "rejection_samples": query_samples,
            }

        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "serper_invalid_json",
                query=query,
                error=str(exc),
                message=f"Serper returned invalid JSON for '{query}'",
            )
            query_drop_reasons["invalid_json"] += 1
            return {
                "query": query,
                "ok": False,
                "raw_results": 0,
                "results": [],
                "borderline_results": [],
                "drop_reasons": query_drop_reasons,
                "rejection_samples": query_samples,
            }

        results = []
        organic = data.get('organic') or []
        for result in organic:
            url = result.get('link', '')
            # Skip blocked domains before any scoring or DB lookup
            if _is_blocked_domain(url):
                query_drop_reasons["blocked_domain"] += 1
                _append_rejection_sample(
                    query_samples,
                    query_sample_keys,
                    reason="blocked_domain",
                    query=query,
                    result=result,
                    limit=sample_limit,
                )
                continue
            # Skip if seen before
            if is_site_seen(url):
                query_drop_reasons["already_seen"] += 1
                _append_rejection_sample(
                    query_samples,
                    query_sample_keys,
                    reason="already_seen",
                    query=query,
                    result=result,
                    limit=sample_limit,
                )
                continue
            
            # Score Meh vibe
            title = result.get('title', '')
            snippet = result.get('snippet', '')
            vibe_score = score_meh_vibe(title, snippet)
            candidate = {
                'title': title,
                'link': url,
                'snippet': snippet,
                'vibe_score': vibe_score,
                'discovery_source': 'search',
                'search_query': query,
            }
            
            # Only include if score >= threshold
            if vibe_score >= strict_vibe_threshold:
                results.append(candidate)
            elif vibe_score >= fallback_vibe_threshold:
                query_drop_reasons["borderline_vibe"] += 1
                query_borderline_results.append(candidate)
                _append_rejection_sample(
                    query_samples,
                    query_sample_keys,
                    reason="borderline_vibe",
                    query=query,
                    result=result,
                    limit=sample_limit,
                    vibe_score=vibe_score,
                )
            else:
                query_drop_reasons["low_vibe"] += 1
                _append_rejection_sample(
                    query_samples,
                    query_sample_keys,
                    reason="low_vibe",
                    query=query,
                    result=result,
                    limit=sample_limit,
                    vibe_score=vibe_score,
                )

        return {
            "query": query,
            "ok": True,
            "raw_results": len(organic),
            "results": results,
            "borderline_results": query_borderline_results,
            "drop_reasons": query_drop_reasons,
            "rejection_samples": query_samples,
        }
    
    # Run all queries in parallel
    all_results = []
    borderline_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_query = {executor.submit(search_query, query): query for query in Config.SEARCH_QUERIES}
        for future in as_completed(future_to_query):
            query = future_to_query[future]
            try:
                query_result = future.result()
                diagnostics["queries_succeeded" if query_result["ok"] else "queries_failed"] += 1
                diagnostics["raw_search_results"] += query_result["raw_results"]
                query_candidates[query] = len(query_result["results"])
                query_borderline_candidates[query] = len(query_result["borderline_results"])
                drop_reasons.update(query_result["drop_reasons"])
                all_results.extend(query_result["results"])
                borderline_results.extend(query_result["borderline_results"])
                for sample in query_result["rejection_samples"]:
                    if len(rejection_samples) >= sample_limit:
                        break
                    sample_key = (
                        sample["reason"],
                        normalize_url(sample.get("url", "")) or sample.get("title", ""),
                    )
                    if sample_key in rejection_sample_keys:
                        continue
                    rejection_samples.append(sample)
                    rejection_sample_keys.add(sample_key)
            except Exception as exc:
                diagnostics["queries_failed"] += 1
                drop_reasons["query_retry_exhausted"] += 1
                query_candidates[query] = 0
                query_borderline_candidates[query] = 0
                logger.error(
                    "serper_query_failed",
                    query=query,
                    error=str(exc),
                    message=f"Serper query failed after retries: {query}",
                )

    relaxed_vibe_threshold_used = False
    promoted_borderline_candidates = 0
    if diagnostics["queries_succeeded"] > 0 and len(all_results) < min_live_candidates and borderline_results:
        relaxed_vibe_threshold_used = True
        promoted_borderline_candidates = len(borderline_results)
        all_results.extend(borderline_results)
        logger.info(
            "search_relaxed_threshold",
            "Relaxed search vibe threshold to recover low-yield discovery",
            strict_vibe_threshold=strict_vibe_threshold,
            fallback_vibe_threshold=fallback_vibe_threshold,
            promoted_borderline_candidates=promoted_borderline_candidates,
        )
    
    # Remove duplicates and sort by vibe score
    seen = set()
    unique_results = []
    for r in all_results:
        normalized_link = normalize_url(r['link'])
        if normalized_link not in seen:
            seen.add(normalized_link)
            unique_results.append(r)
        else:
            drop_reasons["duplicate_url"] += 1
    
    # Sort by vibe score (highest first)
    unique_results.sort(key=lambda x: x['vibe_score'], reverse=True)

    # ── Apply source status rules ─────────────────────────────────────────────
    # keep / new  → normal priority
    # quarantine  → moved to end of list (scanned last, dropped if cap is hit first)
    # remove      → skipped entirely unless the domain is in force_domains
    normal, quarantined = [], []
    for r in unique_results:
        domain = _db_extract_domain(r['link'])
        status = get_source_status(domain)
        r["source_status"] = status
        r["force_included"] = domain in force_domains
        if domain in force_domains:
            normal.append(r)          # manual override: always include at normal priority
            drop_reasons["force_included"] += 1
            continue
        if status == 'remove':
            drop_reasons["source_status_remove"] += 1
            logger.info(
                "source_skipped_remove",
                f"Skipping {domain} (status=remove)",
                domain=domain, status=status,
            )
            continue
        elif status == 'quarantine':
            drop_reasons["source_status_quarantine"] += 1
            quarantined.append(r)     # still eligible, but lower priority
        else:                         # keep or new
            normal.append(r)
    unique_results = normal + quarantined

    # ── Inject seed sites (known-good US deal sites) ─────────────────────────
    seed_seen = {normalize_url(r['link']) for r in unique_results}
    for seed in Config.SEED_DEAL_SITES:
        url = seed['link']
        if _is_blocked_domain(url):
            drop_reasons["seed_blocked_domain"] += 1
            continue
        normalized_seed = normalize_url(url)
        if normalized_seed not in seed_seen:
            seed_entry = {
                **seed,
                'vibe_score': 7,
                'discovery_source': 'seed',
                'search_query': '',
                'source_status': get_source_status(_db_extract_domain(url)),
                'force_included': _db_extract_domain(url) in force_domains,
            }   # seeds get priority vibe score
            unique_results.insert(0, seed_entry)       # seeds go to front of queue
            seed_seen.add(normalized_seed)
        else:
            drop_reasons["seed_duplicate"] += 1

    search_pool = sum(1 for r in unique_results if r.get("discovery_source") == "search")
    seed_pool = sum(1 for r in unique_results if r.get("discovery_source") == "seed")
    if len(unique_results) > Config.MAX_CANDIDATES_PER_RUN:
        drop_reasons["over_max_candidates"] += len(unique_results) - Config.MAX_CANDIDATES_PER_RUN

    final_results = unique_results[:Config.MAX_CANDIDATES_PER_RUN]
    selected_search = sum(1 for r in final_results if r.get("discovery_source") == "search")
    selected_seed = sum(1 for r in final_results if r.get("discovery_source") == "seed")
    live_discovery_empty = selected_search == 0
    degraded_search = diagnostics["queries_failed"] > 0 and live_discovery_empty
    low_yield_search = diagnostics["queries_succeeded"] > 0 and live_discovery_empty

    diagnostics.update({
        "query_candidates": query_candidates,
        "query_borderline_candidates": query_borderline_candidates,
        "drop_reasons": dict(drop_reasons),
        "rejection_samples": rejection_samples,
        "candidate_pool": len(unique_results),
        "selected_candidates": len(final_results),
        "search_candidates_pool": search_pool,
        "seed_candidates_pool": seed_pool,
        "selected_search_candidates": selected_search,
        "selected_seed_candidates": selected_seed,
        "live_discovery_empty": live_discovery_empty,
        "degraded_search": degraded_search,
        "low_yield_search": low_yield_search,
        "relaxed_vibe_threshold_used": relaxed_vibe_threshold_used,
        "promoted_borderline_candidates": promoted_borderline_candidates,
    })
    # Add a timestamp for this diagnostics snapshot
    diagnostics["search_ts"] = datetime.now(timezone.utc).isoformat()
    _LAST_SEARCH_DIAGNOSTICS = diagnostics

    log_search_complete(
        len(final_results),
        fallback_vibe_threshold if relaxed_vibe_threshold_used else strict_vibe_threshold,
    )
    logger.info(
        "search_diagnostics",
        "Search diagnostics recorded",
        **diagnostics,
    )
    if rejection_samples:
        logger.info(
            "search_rejection_samples",
            "Sampled rejected search results",
            samples=rejection_samples,
        )
    if degraded_search:
        logger.warning(
            "search_degraded",
            "Live search degraded; current candidates are coming only from curated seeds",
            queries_failed=diagnostics["queries_failed"],
            selected_seed_candidates=selected_seed,
        )
    elif low_yield_search:
        logger.warning(
            "search_low_yield",
            "Live search succeeded but found no non-seed discoveries",
            queries_succeeded=diagnostics["queries_succeeded"],
            drop_reasons=dict(drop_reasons),
        )

    # Log individual sites
    for result in final_results:
        log_site_scraped(result['link'], result.get('vibe_score', 4))

    return final_results


def _screenshot_path_for_url(url: str) -> Path:
    slug = hashlib.sha256(url.encode()).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return SCREENSHOTS_DIR / f"{stamp}_{slug}.png"


def _collect_meh_signals(text: str) -> str:
    if not text:
        return ""
    low = text.lower()
    found = [k for k in MEH_SIGNAL_KEYWORDS if k in low]
    return ", ".join(dict.fromkeys(found))


def _a11y_collect_hints(snapshot: dict | None) -> list[str]:
    if not snapshot or not isinstance(snapshot, dict):
        return []
    hints: list[str] = []

    def walk(node: dict) -> None:
        name = node.get("name")
        if isinstance(name, str) and 2 < len(name) < 240:
            nl = name.lower()
            if "$" in name or "deal" in nl or "sale" in nl or "price" in nl:
                hints.append(name.strip())
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    walk(snapshot)
    return hints[:10]


def _price_from_ld_item(item: dict) -> str:
    """Extract a price string from a JSON-LD Product or Offer dict."""
    # Direct price on the item (common on Offer nodes)
    for key in ("price", "lowPrice"):
        val = item.get(key)
        if val is not None:
            val = str(val).strip()
            if re.search(r"[0-9]", val):
                return val
    # Recurse into nested offers / offers list
    for field in ("offers", "Offers"):
        child = item.get(field)
        if isinstance(child, dict):
            result = _price_from_ld_item(child)
            if result:
                return result
        elif isinstance(child, list):
            for c in child:
                if isinstance(c, dict):
                    result = _price_from_ld_item(c)
                    if result:
                        return result
    return ""


def _extract_from_soup(soup: BeautifulSoup, url: str) -> dict:
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = re.sub(r"\s+", " ", h1.get_text(strip=True))[:500]
    if not title and soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", soup.title.string.strip())[:500]

    price = ""
    price_el = soup.select_one('[itemprop="price"]')
    if price_el:
        price = (price_el.get("content") or price_el.get_text(strip=True) or "").strip()
    if not price:
        m = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", soup.get_text(" ", strip=True))
        if m:
            price = m.group(0).strip()

    # ── Fallback 1: JSON-LD structured data (Product / Offer) ──────────────
    if not price:
        for script_tag in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script_tag.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if not isinstance(item, dict):
                    continue
                price = _price_from_ld_item(item)
                if price:
                    break
            if price:
                break

    # ── Fallback 2: OpenGraph / product meta tags ──────────────────────────
    if not price:
        for prop in ("og:price:amount", "product:price:amount", "product:price"):
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content", "").strip():
                price = tag["content"].strip()
                break

    # ── Fallback 3: elements with "price" in class name ────────────────────
    if not price:
        for el in soup.find_all(class_=re.compile(r"price", re.I)):
            t = el.get_text(strip=True)
            m = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", t)
            if m:
                price = m.group(0).strip()
                break

    # ── Fix 2: original_price — strikethrough / compare-at patterns ──────────
    original_price = ""
    # Prefer semantic strikethrough tags that sites use for "was" prices
    for sel in ("del", "s"):
        el = soup.find(sel)
        if el:
            t = el.get_text(strip=True)
            if re.search(r"[\$£€]?\s*[0-9]", t):   # must look like a price
                original_price = re.sub(r"\s+", " ", t)[:80]
                break
    if not original_price:
        # Class-name patterns common on Shopify / WooCommerce / meh-style deal sites
        for css in (
            ".original-price",
            ".was-price",
            ".compare-at",
            ".compare-at-price",
            "[class*='original']",
            "[class*='was-price']",
            "[class*='compare']",
            # meh.com and similar sites: retail / list price shown beside sale price
            "span.list-price",
            "[class*='list-price']",
            "[class*='retail']",
        ):
            el = soup.select_one(css)
            if el:
                t = el.get_text(strip=True)
                if re.search(r"[\$£€]?\s*[0-9]", t):
                    original_price = re.sub(r"\s+", " ", t)[:80]
                    break

    # ── Fix 1: promo_copy — strip boilerplate before extracting text ─────────
    main = soup.find("main") or soup.find("article") or soup.body
    promo = ""
    if main:
        # Remove noisy boilerplate tags in-place on a copy so title/price
        # extraction above is unaffected (they already ran against the full soup).
        import copy as _copy
        main_clean = _copy.copy(main)
        for tag in main_clean.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        promo = re.sub(r"\s+", " ", main_clean.get_text(" ", strip=True))[:2000]

    signals = _collect_meh_signals(f"{title} {promo} {price}")
    return {
        "deal_title":     title,
        "deal_price":     price,
        "original_price": original_price,
        "promo_copy":     promo,
        "meh_signals":    signals,
    }


def _humanize_page_interaction(page) -> None:
    try:
        vw = page.viewport_size or {"width": 1280, "height": 720}
        w, h = int(vw["width"]), int(vw["height"])
        page.mouse.move(random.randint(20, max(21, w // 3)), random.randint(20, max(21, h // 3)))
        time.sleep(random.uniform(0.08, 0.28))
        page.mouse.wheel(0, random.randint(80, 420))
        time.sleep(random.uniform(0.12, 0.35))
    except Exception as e:
        logger.debug("humanize_page_skipped", str(e))


def _install_resource_block(context) -> None:
    def handle_route(route, request):
        try:
            if request.resource_type in BLOCKED_RESOURCE_TYPES:
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    context.route("**/*", lambda route: handle_route(route, route.request))


def _goto_with_retries(page, url: str) -> None:
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            time.sleep(random.uniform(0.35, 1.25))
            return
        except Exception as e:
            last_err = e
            logger.warning(
                "playwright_goto_retry",
                url=url,
                attempt=attempt + 1,
                error=str(e),
                message=f"goto retry {attempt + 1} for {url}",
            )
            time.sleep(1.0 + attempt)
    if last_err:
        raise last_err


def _extract_from_playwright_page(page, url: str) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    _goto_with_retries(page, url)
    _humanize_page_interaction(page)

    deal_title = ""
    try:
        deal_title = re.sub(r"\s+", " ", page.locator("h1").first.inner_text(timeout=3000)).strip()[:500]
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    parsed = _extract_from_soup(soup, url)
    if deal_title:
        parsed["deal_title"] = deal_title

    a11y_hints: list[str] = []
    try:
        snap = page.accessibility.snapshot(interesting_only=True)
        a11y_hints = _a11y_collect_hints(snap)
    except Exception as e:
        logger.debug("a11y_snapshot_skipped", str(e), url=url)

    extra_signals = _collect_meh_signals(" ".join(a11y_hints))
    if extra_signals:
        base = parsed.get("meh_signals") or ""
        parsed["meh_signals"] = ", ".join(dict.fromkeys((base + ", " + extra_signals).split(", ")))

    shot_path_str = ""
    path = _screenshot_path_for_url(url)
    for sel in ["main", "[role='main']", "article", "[data-product]", "[class*='product']", "body"]:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            loc.screenshot(path=str(path), timeout=8000)
            shot_path_str = str(path)
            break
        except Exception:
            continue
    if not shot_path_str:
        page.screenshot(path=str(path), full_page=False)
        shot_path_str = str(path)

    parsed["screenshot_path"] = shot_path_str
    parsed["scrape_method"] = "playwright"
    parsed["scrape_error"] = ""
    logger.info(
        "deal_page_playwright_ok",
        message="Playwright extraction complete",
        url=url,
        screenshot_path=shot_path_str,
        has_title=bool(parsed.get("deal_title")),
    )
    return parsed


def scrape_deal_page_requests(url: str) -> dict:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    logger.info("deal_page_http_start", message="HTTP fallback scrape", url=url)
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        data = _extract_from_soup(soup, url)
        data["screenshot_path"] = ""
        data["scrape_method"] = "requests"
        data["scrape_error"] = ""
        logger.info("deal_page_http_ok", "HTTP scrape OK", url=url, has_title=bool(data.get("deal_title")))
        return data
    except Exception as e:
        logger.error("deal_page_http_failed", url=url, error=str(e), message=str(e))
        return {
            "deal_title": "",
            "deal_price": "",
            "promo_copy": "",
            "meh_signals": "",
            "screenshot_path": "",
            "scrape_method": "failed",
            "scrape_error": str(e),
        }


def scrape_deal_page(url: str) -> dict:
    """Scrape a single URL: Playwright first, then requests + BeautifulSoup."""
    logger.info("deal_page_scrape_start", message="Scraping deal page", url=url)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport=random.choice(VIEWPORTS),
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                _install_resource_block(context)
                page = context.new_page()
                try:
                    return _extract_from_playwright_page(page, url)
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()
    except Exception as e:
        logger.warning(
            "playwright_scrape_failed",
            url=url,
            error=str(e),
            message="Falling back to HTTP fetch",
        )
        return scrape_deal_page_requests(url)


def enrich_candidates(sites: list[dict]) -> list[dict]:
    """
    Fetch structured deal fields + screenshots (one browser, sequential pages).
    Playwright is not used concurrently; keep this phase serial before parallel LLM calls.
    """
    if not sites:
        return []
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    enriched: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                for site in sites:
                    url = site.get("link", "")
                    if not url:
                        enriched.append({
                            **site,
                            "deal_title": "",
                            "deal_price": "",
                            "promo_copy": "",
                            "meh_signals": "",
                            "screenshot_path": "",
                            "scrape_method": "skipped",
                            "scrape_error": "missing link",
                        })
                        continue
                    try:
                        context = browser.new_context(
                            user_agent=random.choice(USER_AGENTS),
                            viewport=random.choice(VIEWPORTS),
                            locale="en-US",
                            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                        )
                        _install_resource_block(context)
                        page = context.new_page()
                        try:
                            extra = _extract_from_playwright_page(page, url)
                        except Exception as e:
                            logger.warning(
                                "playwright_page_failed",
                                url=url,
                                error=str(e),
                                message="Per-page Playwright failed; using HTTP fallback",
                            )
                            extra = scrape_deal_page_requests(url)
                        finally:
                            page.close()
                            context.close()
                        enriched.append({**site, **extra})
                    except Exception as e:
                        logger.error("enrich_site_failed", url=url, error=str(e), message=str(e))
                        enriched.append({**site, **scrape_deal_page_requests(url)})
            finally:
                browser.close()
    except Exception as e:
        logger.error("enrich_browser_failed", error=str(e), message="Browser launch failed; HTTP-only enrich")
        for site in sites:
            url = site.get("link", "")
            if not url:
                extra = {
                    "deal_title": "",
                    "deal_price": "",
                    "promo_copy": "",
                    "meh_signals": "",
                    "screenshot_path": "",
                    "scrape_method": "skipped",
                    "scrape_error": "missing link",
                }
            else:
                extra = scrape_deal_page_requests(url)
            enriched.append({**site, **extra})
    return enriched


# Quick test
if __name__ == "__main__":
    results = search_for_deal_sites()
    logger.info("test_results", f"Test completed with {len(results)} results", count=len(results))
