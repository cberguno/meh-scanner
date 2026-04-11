import hashlib
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from db import get_source_status, _extract_domain as _db_extract_domain
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

# SQLite setup for deduplication
DB_PATH = Path("seen_sites.db")

def normalize_url(url):
    """Normalize URL for deduplication: lowercase, strip trailing slash, remove query params"""
    url = url.lower().strip()
    url = re.sub(r'/+$', '', url)  # Remove trailing slashes
    url = re.sub(r'\?.*$', '', url)  # Remove query parameters
    return url

def init_db():
    """Initialize SQLite database for tracking seen sites"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_sites (
            url TEXT PRIMARY KEY,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP
        )
    ''')
    
    # Clean up entries older than 120 days
    cutoff_date = (datetime.now() - timedelta(days=1)).isoformat()
    cursor.execute('DELETE FROM seen_sites WHERE last_seen < ?', (cutoff_date,))
    deleted = cursor.rowcount
    if deleted > 0:
        logger.info("db_cleanup", deleted=deleted, message=f"Cleaned up {deleted} old entries from seen_sites.db")
    
    conn.commit()
    conn.close()

def is_site_seen(url):
    """Check if site has been seen before"""
    normalized_url = normalize_url(url)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM seen_sites WHERE url = ?', (normalized_url,))
    seen = cursor.fetchone() is not None
    conn.close()
    return seen

def mark_site_seen(url):
    """Mark site as seen"""
    normalized_url = normalize_url(url)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute('''
        INSERT OR REPLACE INTO seen_sites (url, first_seen, last_seen)
        VALUES (?, COALESCE((SELECT first_seen FROM seen_sites WHERE url = ?), ?), ?)
    ''', (normalized_url, normalized_url, now, now))
    conn.commit()
    conn.close()

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


def search_for_deal_sites(force_domains: frozenset = frozenset()):
    """Search for potential one-sale-a-day sites using Serper (parallelized)"""
    init_db()  # Initialize SQLite DB
    log_search_start(len(Config.SEARCH_QUERIES))
    
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
        payload = {"q": query, "num": 10}
        try:
            response = requests.post("https://google.serper.dev/search", headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = []
                if 'organic' in data:
                    for result in data['organic']:
                        url = result.get('link', '')
                        # Skip blocked domains before any scoring or DB lookup
                        if _is_blocked_domain(url):
                            continue
                        # Skip if seen before
                        if is_site_seen(url):
                            continue
                        
                        # Score Meh vibe
                        title = result.get('title', '')
                        snippet = result.get('snippet', '')
                        vibe_score = score_meh_vibe(title, snippet)
                        
                        # Only include if score >= 3
                        if vibe_score >= 3:
                            results.append({
                                'title': title,
                                'link': url,
                                'snippet': snippet,
                                'vibe_score': vibe_score
                            })
                return results
            else:
                logger.error("serper_error", query=query, status_code=response.status_code, message=f"Serper error for '{query}': {response.status_code}")
                return []
        except Exception as e:
            logger.error("serper_request_failed", query=query, error=str(e), message=f"Request failed for '{query}': {str(e)}")
            return []
    
    # Run all queries in parallel
    all_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_query = {executor.submit(search_query, query): query for query in Config.SEARCH_QUERIES}
        for future in as_completed(future_to_query):
            all_results.extend(future.result())
    
    # Remove duplicates and sort by vibe score
    seen = set()
    unique_results = []
    for r in all_results:
        if r['link'] not in seen:
            seen.add(r['link'])
            unique_results.append(r)
    
    # Sort by vibe score (highest first)
    unique_results.sort(key=lambda x: x['vibe_score'], reverse=True)

    # ── Apply source status rules ─────────────────────────────────────────────
    # keep / new  → normal priority
    # quarantine  → moved to end of list (scanned last, dropped if cap is hit first)
    # remove      → skipped entirely unless the domain is in force_domains
    normal, quarantined = [], []
    for r in unique_results:
        domain = _db_extract_domain(r['link'])
        if domain in force_domains:
            normal.append(r)          # manual override: always include at normal priority
            continue
        status = get_source_status(domain)
        if status == 'remove':
            logger.info(
                "source_skipped_remove",
                f"Skipping {domain} (status=remove)",
                domain=domain, status=status,
            )
            continue
        elif status == 'quarantine':
            quarantined.append(r)     # still eligible, but lower priority
        else:                         # keep or new
            normal.append(r)
    unique_results = normal + quarantined

    log_search_complete(len(unique_results), 3)
    
    # Log individual sites
    for result in unique_results:
        log_site_scraped(result['link'], result['vibe_score'])
    
    # Mark sites as seen and return top candidates
    for site in unique_results[:Config.MAX_CANDIDATES_PER_RUN]:
        mark_site_seen(site['link'])
    
    return unique_results[:Config.MAX_CANDIDATES_PER_RUN]


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