import hashlib
import random
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from db import init_db, is_site_seen, mark_site_seen, get_source_status, _extract_domain as _db_extract_domain, _normalize_url_for_seen as normalize_url
from logger import logger, log_search_start, log_search_complete, log_site_scraped

# ---------------------------------------------------------------------------
# Optional structured-data libraries — fail gracefully if not installed so
# the module still imports without them (useful in environments where the full
# requirements haven't been installed yet).
# ---------------------------------------------------------------------------
try:
    import extruct  # type: ignore
    from w3lib.html import get_base_url  # type: ignore
    _HAS_EXTRUCT = True
except ImportError:  # pragma: no cover
    extruct = None  # type: ignore
    get_base_url = None  # type: ignore
    _HAS_EXTRUCT = False

try:
    from price_parser import Price as _PriceParser  # type: ignore
    _HAS_PRICE_PARSER = True
except ImportError:  # pragma: no cover
    _PriceParser = None  # type: ignore
    _HAS_PRICE_PARSER = False

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

# ---------------------------------------------------------------------------
# Structured-data extraction helpers
# ---------------------------------------------------------------------------

# Minimal TLD → ISO-4217 currency inference fallback used when priceCurrency
# is absent from the page's structured data.
_TLD_CURRENCY_MAP: dict[str, str] = {
    ".co.uk": "GBP",
    ".uk":    "GBP",
    ".de":    "EUR",
    ".fr":    "EUR",
    ".es":    "EUR",
    ".it":    "EUR",
    ".nl":    "EUR",
    ".eu":    "EUR",
    ".ca":    "CAD",
    ".au":    "AUD",
    ".jp":    "JPY",
    ".us":    "USD",
    ".com":   "USD",
    ".co":    "USD",
}

# Map common currency symbols / informal codes returned by price-parser to
# their ISO-4217 equivalents so we never store raw symbols like "$".
_CURRENCY_SYMBOL_MAP: dict[str, str] = {
    "$":   "USD",
    "us$": "USD",
    "usd": "USD",
    "€":   "EUR",
    "eur": "EUR",
    "£":   "GBP",
    "gbp": "GBP",
    "¥":   "JPY",
    "jpy": "JPY",
    "₹":   "INR",
    "inr": "INR",
    "a$":  "AUD",
    "aud": "AUD",
    "c$":  "CAD",
    "cad": "CAD",
}

# Regex patterns for GTIN / MPN / SKU extraction from raw HTML text.
_GTIN_RE = re.compile(
    r'\b(?:gtin(?:8|12|13|14)?|ean|upc|isbn)\s*[:\s=]\s*([0-9]{6,14})\b',
    re.IGNORECASE,
)
_MPN_RE = re.compile(
    r'\b(?:mpn|model(?:\s*number)?|sku|part(?:\s*number)?)\s*[:\s=]\s*([A-Za-z0-9][-A-Za-z0-9]{3,})\b',
    re.IGNORECASE,
)
# Broader price-with-symbol pattern used only as last-resort fallback.
_PRICE_SYMBOL_RE = re.compile(
    r'([$€£₹¥]\s?\d[\d,\.]{0,}|\d[\d,\.]{0,}\s?(?:USD|EUR|GBP|CAD|AUD|JPY))',
    re.IGNORECASE,
)

# Fields considered "complete" for scoring purposes.
_COMPLETENESS_FIELDS = ("deal_title", "deal_price", "product_image", "brand", "currency")


def _infer_currency_from_url(url: str) -> Optional[str]:
    """Return a best-guess ISO currency code derived from the URL's TLD.

    Iterates ``_TLD_CURRENCY_MAP`` from most-specific to least-specific suffix
    so ``.co.uk`` matches before ``.uk``.
    """
    try:
        hostname = urlparse(url).hostname or ""
        for tld, currency in _TLD_CURRENCY_MAP.items():
            if hostname.endswith(tld):
                return currency
    except Exception:
        pass
    return None


def _extract_structured_data(html: str, url: str) -> dict[str, Any]:
    """Extract JSON-LD, microdata, and OpenGraph structured data from *html*.

    Returns a dict with keys ``json-ld``, ``microdata``, ``opengraph``.
    Falls back to an empty dict when extruct is unavailable or raises.
    """
    if not _HAS_EXTRUCT or not html:
        return {}
    try:
        base_url = get_base_url(html, url)
        return extruct.extract(
            html,
            base_url=base_url,
            syntaxes=["json-ld", "microdata", "opengraph"],
            uniform=True,
        )
    except Exception as exc:
        logger.debug("extruct_failed", str(exc), url=url)
        return {}


def _find_product_jsonld(structured: dict) -> dict[str, Any]:
    """Return the first schema.org Product node from JSON-LD data (or {})."""
    for item in structured.get("json-ld") or []:
        types = item.get("@type") or item.get("type") or ""
        if isinstance(types, list):
            types = " ".join(types)
        if "Product" in str(types):
            return item
    return {}


def _find_product_microdata(structured: dict) -> dict[str, Any]:
    """Return the first schema.org Product node from microdata (or {})."""
    for item in structured.get("microdata") or []:
        types = item.get("@type") or item.get("type") or ""
        if isinstance(types, list):
            types = " ".join(types)
        if "Product" in str(types):
            return item
    return {}


def _select_best_offer(offers: Any) -> dict[str, Any]:
    """Normalise a single offer dict or a list of offers to one best offer.

    "Best" here means the first offer that carries a ``price`` field.  When
    *offers* is a list, the offer with the lowest ``price`` value is preferred
    to surface the best current deal.
    """
    if not offers:
        return {}
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        candidates = [o for o in offers if isinstance(o, dict) and o.get("price") is not None]
        if not candidates:
            return offers[0] if offers else {}
        try:
            return min(candidates, key=lambda o: _parse_price_text(str(o["price"])).get("amount") or float("inf"))
        except (ValueError, TypeError):
            return candidates[0]
    return {}


def _parse_price_text(text: Optional[str]) -> dict[str, Any]:
    """Parse a raw price string into ``{amount, currency}`` using price-parser.

    Currency symbols are normalized to ISO-4217 codes using
    ``_CURRENCY_SYMBOL_MAP`` so callers always receive a standardized code
    (e.g. ``"USD"`` instead of ``"$"``).

    Falls back to a simple regex extraction when price-parser is unavailable.
    Returns an empty dict when parsing fails.
    """
    if not text:
        return {}
    if _HAS_PRICE_PARSER:
        try:
            parsed = _PriceParser.fromstring(str(text))
            if parsed.amount is not None:
                raw_cur = (parsed.currency or "").strip()
                # Normalize symbol to ISO code; fall back to uppercased value, or None for empty.
                currency = _CURRENCY_SYMBOL_MAP.get(raw_cur.lower(), raw_cur.upper() if raw_cur else None)
                return {"amount": float(parsed.amount), "currency": currency}
        except Exception:
            pass
    # Regex fallback: grab the first symbol+number pattern
    m = _PRICE_SYMBOL_RE.search(str(text))
    if m:
        raw = m.group(0).strip()
        digits = re.sub(r"[^\d\.]", "", raw.replace(",", ""))
        try:
            return {"amount": float(digits), "currency": None}
        except ValueError:
            pass
    return {}


def _extract_price_from_product(product: dict[str, Any]) -> dict[str, Any]:
    """Pull price / currency out of a schema.org Product node.

    Handles both a single ``offers`` dict and a list of offers, including
    the nested ``priceCurrency`` field.  Currency values are normalized to
    ISO-4217 codes via ``_CURRENCY_SYMBOL_MAP``.
    """
    offer = _select_best_offer(product.get("offers"))
    price_raw = (offer.get("price") or offer.get("lowPrice") or "")
    currency_raw = (offer.get("priceCurrency") or offer.get("currency") or "").strip()
    # Normalize symbol to ISO code; fall back to uppercased value, or None for empty.
    currency = _CURRENCY_SYMBOL_MAP.get(currency_raw.lower(), currency_raw.upper() if currency_raw else None)
    result = _parse_price_text(str(price_raw)) if price_raw else {}
    if currency and not result.get("currency"):
        result["currency"] = currency
    return result


def _extract_gtin(product: dict[str, Any], html: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(gtin, mpn)`` extracted from *product* structured data or *html*.

    Priority order: JSON-LD fields → offer fields → HTML regex.
    """
    # Standard schema.org GTIN fields (most specific first)
    for field in ("gtin13", "gtin12", "gtin8", "gtin14", "gtin", "isbn"):
        val = product.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip(), product.get("mpn") or _extract_mpn_html(html)

    # MPN / SKU in the offer
    offer = _select_best_offer(product.get("offers"))
    gtin_from_offer = offer.get("gtin13") or offer.get("gtin") or offer.get("isbn")
    if gtin_from_offer:
        return str(gtin_from_offer).strip(), product.get("mpn") or _extract_mpn_html(html)

    # HTML-level regex fallback
    gtin_m = _GTIN_RE.search(html)
    if gtin_m:
        return gtin_m.group(1).strip(), _extract_mpn_html(html)

    return None, _extract_mpn_html(html)


def _extract_mpn_html(html: str) -> Optional[str]:
    """Return MPN/SKU from raw HTML using a regex heuristic, or None."""
    m = _MPN_RE.search(html)
    return m.group(1).strip() if m else None


def _extract_brand(product: dict[str, Any], soup: BeautifulSoup) -> Optional[str]:
    """Return the product brand from structured data or HTML meta tags."""
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("@name")
    if brand and isinstance(brand, str):
        return brand.strip()
    # manufacturer field (schema.org alternative)
    manufacturer = product.get("manufacturer")
    if isinstance(manufacturer, dict):
        manufacturer = manufacturer.get("name") or manufacturer.get("@name")
    if manufacturer and isinstance(manufacturer, str):
        return manufacturer.strip()
    # OpenGraph or meta brand tag
    brand_meta = soup.find("meta", attrs={"property": "product:brand"}) or \
                 soup.find("meta", attrs={"name": "brand"})
    if brand_meta and brand_meta.get("content"):
        return brand_meta["content"].strip()
    return None


def _extract_image(product: dict[str, Any], soup: BeautifulSoup) -> Optional[str]:
    """Return the best product image URL from structured data or OG meta tags."""
    img = product.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url") or img.get("@id")
    if img and isinstance(img, str):
        return img.strip()
    # OpenGraph fallback
    og_img = soup.find("meta", attrs={"property": "og:image"})
    if og_img and og_img.get("content"):
        return og_img["content"].strip()
    return None


def _validate_image_url(image_url: Optional[str], timeout: int = 5) -> bool:
    """Lightweight HEAD check: verify *image_url* exists and is an image type.

    Returns True when the URL responds with a 2xx status and an image
    content-type.  Silently returns False on any error so extraction never
    blocks on a network call.
    """
    if not image_url:
        return False
    try:
        resp = requests.head(image_url, timeout=timeout, allow_redirects=True)
        content_type = resp.headers.get("content-type", "")
        return resp.ok and "image" in content_type.lower()
    except Exception:
        return False


def _compute_completeness_score(record: dict[str, Any]) -> float:
    """Return a 0.0–1.0 completeness fraction based on key product fields.

    A higher score means more fields are populated; used to rank records and
    decide whether to trigger a re-scrape.

    TODO (future): weight fields differently — price and title matter more
    than MPN or GTIN for deal-site discovery ranking.
    """
    present = sum(1 for f in _COMPLETENESS_FIELDS if record.get(f))
    return round(present / len(_COMPLETENESS_FIELDS), 2)


def _compute_confidence_score(record: dict[str, Any], used_structured_data: bool) -> float:
    """Return a 0.0–1.0 confidence score reflecting extraction reliability.

    Structured-data sources earn a base bonus because they are explicit
    machine-readable annotations rather than heuristic guesses.

    TODO (future): incorporate source reliability_score from the source
    registry DB and image validation results into confidence weighting.
    """
    score = 0.5 if used_structured_data else 0.2
    # Bonus for high completeness
    score += 0.3 * _compute_completeness_score(record)
    # Bonus if we found a GTIN (strongest canonical identifier)
    if record.get("gtin"):
        score += 0.1
    # Bonus if currency is known
    if record.get("currency"):
        score += 0.1
    return round(min(score, 1.0), 2)


def _compute_canonical_key(record: dict[str, Any]) -> str:
    """Return a stable, deterministic key for downstream deduplication.

    Priority: GTIN (strongest) → brand+model → brand+title → title alone.

    TODO (future): replace title-hash fallback with a vector-embedding-based
    cluster key (e.g. CLIP or all-MiniLM) for semantic deduplication across
    merchants that describe the same product differently.
    """
    if record.get("gtin"):
        return f"gtin:{record['gtin'].strip()}"
    brand = (record.get("brand") or "").lower().strip()
    mpn = (record.get("mpn") or "").lower().strip()
    title = (record.get("deal_title") or "").lower().strip()
    # Priority: brand+mpn → brand+title → title alone
    if brand and mpn:
        key_parts = [brand, mpn]
    elif brand:
        key_parts = [brand, title[:80]] if title else [brand]
    elif title:
        key_parts = [title[:80]]
    else:
        key_parts = []
    if key_parts:
        raw_key = "|".join(key_parts)
        return "bm:" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
    return "t:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


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


def _extract_from_soup(soup: BeautifulSoup, url: str, html: str = "") -> dict:
    """Extract product fields from *soup*, with JSON-LD / microdata priority.

    Parameters
    ----------
    soup:
        Parsed HTML document.
    url:
        Final page URL (used for currency inference and structured-data base
        URL resolution).
    html:
        Raw HTML string.  When provided, enables extruct-based structured-data
        parsing (JSON-LD, microdata, OpenGraph).  Falls back to heuristic
        extraction when empty or when extruct is unavailable.

    Returns
    -------
    dict
        Always contains the legacy keys expected by downstream code
        (``deal_title``, ``deal_price``, ``original_price``, ``promo_copy``,
        ``meh_signals``) plus enriched keys added by this improvement:
        ``product_image``, ``brand``, ``gtin``, ``mpn``, ``currency``,
        ``price_amount``, ``completeness_score``, ``confidence_score``,
        ``canonical_key``.
    """
    # ------------------------------------------------------------------
    # 1. Structured-data extraction (JSON-LD → microdata → OpenGraph)
    # ------------------------------------------------------------------
    structured = _extract_structured_data(html or str(soup), url)
    product = _find_product_jsonld(structured)
    used_structured = bool(product)
    if not product:
        product = _find_product_microdata(structured)
        used_structured = bool(product)

    og_data: dict[str, Any] = {}
    for og_item in structured.get("opengraph") or []:
        if isinstance(og_item, dict):
            og_data.update(og_item)

    # ------------------------------------------------------------------
    # 2. Title — JSON-LD → OG → h1 → <title>
    # ------------------------------------------------------------------
    title: str = ""
    if product.get("name"):
        title = str(product["name"]).strip()[:500]
    if not title:
        og_title = og_data.get("og:title") or og_data.get("title")
        if og_title:
            title = str(og_title).strip()[:500]
    if not title:
        og_meta = soup.find("meta", property="og:title")
        if og_meta and og_meta.get("content"):
            title = og_meta["content"].strip()[:500]
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = re.sub(r"\s+", " ", h1.get_text(strip=True))[:500]
    if not title and soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", soup.title.string.strip())[:500]

    # ------------------------------------------------------------------
    # 3. Price / currency — JSON-LD offers → itemprop → regex
    # ------------------------------------------------------------------
    price_obj = _extract_price_from_product(product)
    price_str: str = ""
    # Distinguish between an explicitly stated currency (from structured data)
    # versus a currency guessed from a price symbol — TLD inference should
    # override the latter but never the former.
    currency_explicit: Optional[str] = price_obj.get("currency")    # from JSON-LD/microdata
    currency_symbol: Optional[str] = None                           # from price-text symbol
    price_amount: Optional[float] = price_obj.get("amount")

    if price_amount is not None:
        # Structured data provided a price; format a display string using ISO code.
        cur = currency_explicit or "USD"
        price_str = f"{price_amount:.2f} {cur}"
    else:
        # itemprop microdata fallback
        price_el = soup.select_one('[itemprop="price"]')
        if price_el:
            raw_price = (price_el.get("content") or price_el.get_text(strip=True) or "").strip()
            if raw_price:
                price_obj2 = _parse_price_text(raw_price)
                price_amount = price_obj2.get("amount")
                currency_symbol = price_obj2.get("currency")
                price_str = raw_price
        if not price_str:
            # priceCurrency meta tag (explicit, so treat as authoritative)
            cur_meta = soup.select_one('meta[itemprop="priceCurrency"]')
            if cur_meta and cur_meta.get("content"):
                currency_explicit = currency_explicit or cur_meta["content"].strip().upper()
            # Dollar-sign regex last resort
            m = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", soup.get_text(" ", strip=True))
            if m:
                price_str = m.group(0).strip()
                price_obj3 = _parse_price_text(price_str)
                price_amount = price_obj3.get("amount")
                currency_symbol = currency_symbol or price_obj3.get("currency")

    # Currency priority:
    #   1. Explicit value from structured data or priceCurrency meta tag (most reliable)
    #   2. TLD-inferred value (second most reliable for non-annotated sites)
    #   3. Symbol-derived guess from price text (least reliable: $ ≠ always USD)
    tld_currency = _infer_currency_from_url(url)
    currency: Optional[str] = currency_explicit or tld_currency or currency_symbol

    # ------------------------------------------------------------------
    # 4. Original / compare-at price — strikethrough / class patterns
    # ------------------------------------------------------------------
    original_price = ""
    for sel in ("del", "s"):
        el = soup.find(sel)
        if el:
            t = el.get_text(strip=True)
            if re.search(r"[\$£€]?\s*[0-9]", t):
                original_price = re.sub(r"\s+", " ", t)[:80]
                break
    if not original_price:
        for css in (
            ".original-price",
            ".was-price",
            ".compare-at",
            ".compare-at-price",
            "[class*='original']",
            "[class*='was-price']",
            "[class*='compare']",
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

    # ------------------------------------------------------------------
    # 5. Promo copy — strip boilerplate from main content area
    # ------------------------------------------------------------------
    main = soup.find("main") or soup.find("article") or soup.body
    promo = ""
    if main:
        import copy as _copy
        main_clean = _copy.copy(main)
        for tag in main_clean.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        promo = re.sub(r"\s+", " ", main_clean.get_text(" ", strip=True))[:2000]

    # ------------------------------------------------------------------
    # 6. Brand, image, GTIN / MPN from structured data
    # ------------------------------------------------------------------
    brand = _extract_brand(product, soup)
    product_image = _extract_image(product, soup)
    gtin, mpn = _extract_gtin(product, html or "")

    # ------------------------------------------------------------------
    # 7. Meh signals from full text
    # ------------------------------------------------------------------
    signals = _collect_meh_signals(f"{title} {promo} {price_str}")

    # ------------------------------------------------------------------
    # 8. Quality scoring and canonical key
    # ------------------------------------------------------------------
    base_record: dict[str, Any] = {
        "deal_title":     title,
        "deal_price":     price_str,
        "original_price": original_price,
        "promo_copy":     promo,
        "meh_signals":    signals,
        "product_image":  product_image,
        "brand":          brand,
        "gtin":           gtin,
        "mpn":            mpn,
        "currency":       currency,
        "price_amount":   price_amount,
    }
    completeness = _compute_completeness_score(base_record)
    confidence = _compute_confidence_score(base_record, used_structured_data=used_structured)
    canonical_key = _compute_canonical_key(base_record)

    logger.debug(
        "extract_from_soup",
        f"Extracted: title={bool(title)} price={bool(price_str)} brand={bool(brand)} "
        f"gtin={bool(gtin)} completeness={completeness} confidence={confidence}",
        url=url,
        used_structured_data=used_structured,
        completeness_score=completeness,
        confidence_score=confidence,
    )

    return {
        **base_record,
        "completeness_score": completeness,
        "confidence_score":   confidence,
        "canonical_key":      canonical_key,
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
    parsed = _extract_from_soup(soup, url, html=html)
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
        data = _extract_from_soup(soup, url, html=resp.text)
        data["screenshot_path"] = ""
        data["scrape_method"] = "requests"
        data["scrape_error"] = ""
        logger.info("deal_page_http_ok", "HTTP scrape OK", url=url, has_title=bool(data.get("deal_title")))
        return data
    except Exception as e:
        logger.error("deal_page_http_failed", url=url, error=str(e), message=str(e))
        return {
            "deal_title":         "",
            "deal_price":         "",
            "original_price":     "",
            "promo_copy":         "",
            "meh_signals":        "",
            "product_image":      None,
            "brand":              None,
            "gtin":               None,
            "mpn":                None,
            "currency":           None,
            "price_amount":       None,
            "completeness_score": 0.0,
            "confidence_score":   0.0,
            "canonical_key":      "",
            "screenshot_path":    "",
            "scrape_method":      "failed",
            "scrape_error":       str(e),
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


def _make_skip_record(reason: str) -> dict[str, Any]:
    """Return a minimal 'skipped' scrape record with all expected keys populated."""
    return {
        "deal_title":         "",
        "deal_price":         "",
        "original_price":     "",
        "promo_copy":         "",
        "meh_signals":        "",
        "product_image":      None,
        "brand":              None,
        "gtin":               None,
        "mpn":                None,
        "currency":           None,
        "price_amount":       None,
        "completeness_score": 0.0,
        "confidence_score":   0.0,
        "canonical_key":      "",
        "screenshot_path":    "",
        "scrape_method":      "skipped",
        "scrape_error":       reason,
    }


def enrich_candidates(sites: list[dict]) -> list[dict]:
    """
    Fetch structured deal fields + screenshots (one browser, sequential pages).
    Playwright is not used concurrently; keep this phase serial before parallel LLM calls.

    Each enriched record now includes completeness_score, confidence_score, and
    canonical_key in addition to the legacy scraping fields so downstream
    consumers can filter or rank by data quality.

    TODO (future): expose completeness_score in the dashboard quality panel and
    trigger an automatic re-scrape for records below a configurable threshold.

    TODO (future): use canonical_key + gtin for cross-merchant deduplication
    before writing to Google Sheets.

    TODO (future): implement advanced monitoring — alert when the average
    completeness_score for a daily run drops below a configured threshold.

    TODO (future): cluster products by canonical_key to detect when multiple
    seed sites are featuring the same underlying product on the same day.
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
                        enriched.append({**site, **_make_skip_record("missing link")})
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
                extra: dict[str, Any] = _make_skip_record("missing link")
            else:
                extra = scrape_deal_page_requests(url)
            enriched.append({**site, **extra})
    return enriched


# Quick test
if __name__ == "__main__":
    results = search_for_deal_sites()
    logger.info("test_results", f"Test completed with {len(results)} results", count=len(results))
