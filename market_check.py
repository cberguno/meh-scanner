"""
Market price verification for accepted deals.

For each deal with a deal_title and deal_price, runs a 3-step pipeline:
  1. LLM (Haiku) extracts clean product name + Google Shopping search queries
  2. Serper /shopping API fetches top 5 real-world results
  3. LLM (Haiku) matches results, estimates savings, flags worth_buying

Deals are processed in parallel (ThreadPoolExecutor). Each deal's 3 steps run
sequentially. Generic/login-wall titles (e.g. "Account") are skipped.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from anthropic import Anthropic

from config import Config
from logger import logger

_client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)
_HAIKU = "claude-3-haiku-20240307"
_SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"

# Titles that indicate the scraper hit a login wall or returned garbage
_GENERIC_TITLES = {
    "account", "login", "sign in", "sign up", "log in", "register",
    "dashboard", "home", "shop", "store", "deals",
}


def _is_generic_title(deal: dict) -> bool:
    """Return True if deal_title is missing, too short, generic, or equals site_name."""
    title = (deal.get("deal_title") or "").strip()
    if not title or len(title) < 5:
        return True
    low = title.lower()
    if low in _GENERIC_TITLES:
        return True
    site = (deal.get("site_name") or "").strip().lower()
    if site and low == site:
        return True
    return False


def _identify_product(deal: dict) -> dict | None:
    """
    Step 1: LLM call to extract clean product name and search queries.
    Returns None on failure.
    """
    prompt = f"""You are a product identification assistant for deal site analysis.

Given a daily deal site listing, extract structured product information to enable Google Shopping price comparison.

Site: {deal.get("site_name", "")}
Title: {deal.get("deal_title", "")}
Price: {deal.get("deal_price", "")}
Promo copy: {str(deal.get("promo_copy") or "")[:800]}

Return ONLY valid JSON with no explanation or markdown:
{{
  "product_name": "clean product name, no marketing language",
  "brand": "brand name, or empty string if unknown",
  "model": "model number or SKU if visible, otherwise empty string",
  "search_queries": [
    "most specific query: brand + model + product type",
    "broader fallback: brand + product name"
  ]
}}

Rules:
- Remove adjectives like "amazing", "exclusive", "deal of the day"
- If no brand is visible, leave brand as empty string
- Queries must be suitable for Google Shopping search (short, factual, no site: operators)
- Return exactly 2 search_queries"""

    try:
        response = _client.messages.create(
            model=_HAIKU,
            max_tokens=300,
            temperature=0.0,
            timeout=20.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        logger.warning(
            "market_identify_failed",
            f"Product ID failed for {deal.get('site_name')}: {e}",
        )
        return None


def _serper_shopping(query: str) -> list[dict]:
    """
    Step 2: Query Serper's Google Shopping endpoint.
    Returns up to 5 results with title, price (float|None), source, link.
    """
    try:
        resp = requests.post(
            _SERPER_SHOPPING_URL,
            headers={
                "X-API-KEY": Config.SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": 5},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("shopping", [])
        results = []
        for item in items[:5]:
            raw_price = item.get("price", "")
            try:
                price_f = float(
                    str(raw_price).replace("$", "").replace(",", "").strip()
                )
            except (ValueError, TypeError):
                price_f = None
            results.append(
                {
                    "title": item.get("title", ""),
                    "price": price_f,
                    "price_raw": str(raw_price),
                    "source": item.get("source", ""),
                    "link": item.get("link", ""),
                }
            )
        return results
    except Exception as e:
        logger.warning(
            "market_serper_failed",
            f"Serper shopping failed for {query!r}: {e}",
        )
        return []


def _verify_match(deal: dict, shopping_results: list[dict]) -> dict:
    """
    Step 3: LLM call to match shopping results and assess deal value.
    Returns a dict with market_price, match_confidence, worth_buying, etc.
    """
    if not shopping_results:
        return {
            "best_match_title": "",
            "market_price": None,
            "market_source": "",
            "match_confidence": "low",
            "verified_savings_pct": None,
            "worth_buying": False,
            "reasoning": "No shopping results found for comparison.",
        }

    results_text = json.dumps(
        [
            {"title": r["title"], "price": r["price_raw"], "source": r["source"]}
            for r in shopping_results
        ],
        indent=2,
    )

    prompt = f"""You are a deal verification assistant. Determine whether a daily deal is genuinely discounted vs. regular retail price.

DEAL LISTING:
Site: {deal.get("site_name", "")}
Title: {deal.get("deal_title", "")}
Deal price: ${deal.get("deal_price", "")}

GOOGLE SHOPPING RESULTS (top matches):
{results_text}

Identify the best product match and assess the deal quality. Return ONLY valid JSON with no explanation or markdown:
{{
  "best_match_title": "title of the best matching shopping result, or empty string if no confident match",
  "market_price": <market price as float, e.g. 49.99, or null if no confident match>,
  "market_source": "retailer name (e.g. Amazon, Walmart, Best Buy), or empty string",
  "match_confidence": "high|medium|low",
  "verified_savings_pct": <savings percentage as float 0-100, or null>,
  "worth_buying": true or false,
  "reasoning": "One sentence: what matched, what the market price is, whether it's a real deal"
}}

Savings calculation: verified_savings_pct = (market_price - deal_price) / market_price * 100
If deal_price >= market_price, set verified_savings_pct = 0.

Confidence rules:
- "high": clear brand/model match, price within expected range
- "medium": likely match but product variant or minor uncertainty
- "low": vague title, wildly varying prices, bundle vs. individual, or no clear match

worth_buying rules:
- If deal_price >= market_price: ALWAYS set worth_buying=false, verified_savings_pct=0, and reasoning must state the deal is not cheaper than market
- true ONLY if deal_price < market_price AND match_confidence is "high" or "medium" AND verified_savings_pct >= 15
- false in all other cases"""

    try:
        response = _client.messages.create(
            model=_HAIKU,
            max_tokens=300,
            temperature=0.0,
            timeout=20.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        # ── Programmatic sanity check: don't trust LLM arithmetic ──────
        dp = _parse_price_float(deal.get("deal_price"))
        mp = result.get("market_price")
        if mp is not None:
            try:
                mp = float(mp)
            except (ValueError, TypeError):
                mp = None
        if dp is not None and mp is not None and dp >= mp:
            result["worth_buying"] = False
            result["verified_savings_pct"] = 0
            if "not cheaper" not in (result.get("reasoning") or "").lower():
                result["reasoning"] = (
                    f"Deal ${dp:.2f} is not cheaper than market ${mp:.2f}. "
                    + (result.get("reasoning") or "")
                )
        elif dp is not None and mp is not None and mp > dp:
            # Recompute savings % from code, overriding LLM value
            result["verified_savings_pct"] = round((mp - dp) / mp * 100, 1)

        return result
    except Exception as e:
        logger.warning(
            "market_verify_failed",
            f"Market verify failed for {deal.get('site_name')}: {e}",
        )
        return {
            "best_match_title": "",
            "market_price": None,
            "market_source": "",
            "match_confidence": "low",
            "verified_savings_pct": None,
            "worth_buying": False,
            "reasoning": f"Verification failed: {e}",
        }


def _parse_price_float(s: str | None) -> float | None:
    """Parse a price string like '$29.99' or '29.99' to float. Returns None on failure."""
    if not s:
        return None
    import re
    m = re.search(r"[\d]+(?:[.,]\d+)*", str(s).replace(",", ""))
    if not m:
        return None
    try:
        v = float(m.group())
        return v if v > 0 else None
    except ValueError:
        return None


_NULL_MARKET: dict = {
    "market_price": None,
    "market_source": None,
    "match_confidence": None,
    "verified_savings_pct": None,
    "worth_buying": False,
    "market_reasoning": "",
}


def _check_one_deal(deal: dict) -> dict:
    """Run the full 3-step market check for a single deal. Returns enriched deal dict."""
    if _is_generic_title(deal):
        logger.info(
            "market_skip_generic",
            f"Skipping market check — generic title {deal.get('deal_title')!r} ({deal.get('site_name')})",
        )
        return {**deal, **_NULL_MARKET}

    deal_price_f = _parse_price_float(deal.get("deal_price"))
    if not deal_price_f or deal_price_f <= 0:
        if deal.get("deal_price"):
            logger.info(
                "market_skip_zero_price",
                f"Skipping market check — price ${deal.get('deal_price')!r} parses to 0 or less ({deal.get('site_name')})",
            )
        return {**deal, **_NULL_MARKET}

    # Step 1: identify product + generate search queries
    product_info = _identify_product(deal)
    if not product_info:
        return {**deal, **_NULL_MARKET}

    # Step 2: run up to 2 shopping queries, dedupe results by title
    queries = product_info.get("search_queries") or []
    all_results: list[dict] = []
    seen_titles: set[str] = set()
    for q in queries[:2]:
        if not q:
            continue
        for r in _serper_shopping(q):
            key = r["title"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                all_results.append(r)
        if len(all_results) >= 5:
            break

    # Step 3: verify best match and compute savings
    verification = _verify_match(deal, all_results[:5])

    mp = verification.get("market_price")
    if mp is not None:
        try:
            mp = float(mp)
        except (ValueError, TypeError):
            mp = None

    return {
        **deal,
        "market_price": mp,
        "market_source": verification.get("market_source") or None,
        "match_confidence": verification.get("match_confidence") or None,
        "verified_savings_pct": verification.get("verified_savings_pct"),
        "worth_buying": bool(verification.get("worth_buying", False)),
        "market_reasoning": verification.get("reasoning", ""),
    }


def check_market_prices(deals: list[dict]) -> list[dict]:
    """
    Enrich each deal with market price data.
    Deals whose titles are generic/missing are skipped (market fields set to None).
    Per-deal pipeline runs in parallel; each deal's 3 steps are sequential.
    Returns a new list of enriched deal dicts in original order.
    """
    if not deals:
        return deals

    logger.info(
        "market_check_start",
        f"Market check starting for {len(deals)} deal(s)",
        count=len(deals),
    )

    results_by_idx: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_idx = {
            executor.submit(_check_one_deal, deal): idx
            for idx, deal in enumerate(deals)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results_by_idx[idx] = future.result()
            except Exception as e:
                logger.warning(
                    "market_check_error",
                    f"Market check failed for deal #{idx}: {e}",
                )
                results_by_idx[idx] = {**deals[idx], **_NULL_MARKET}

    enriched = [results_by_idx[i] for i in range(len(deals))]

    worth_count = sum(1 for d in enriched if d.get("worth_buying"))
    high_conf = sum(
        1 for d in enriched if d.get("match_confidence") in ("high", "medium")
    )
    skipped = sum(1 for d in enriched if d.get("match_confidence") is None)
    logger.info(
        "market_check_done",
        f"Market check done: {worth_count}/{len(enriched)} worth buying, "
        f"{high_conf} medium/high confidence, {skipped} skipped",
        worth_buying_count=worth_count,
        high_confidence_count=high_conf,
        skipped_count=skipped,
    )
    return enriched
