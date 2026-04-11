from anthropic import Anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import Config
from logger import logger, log_analysis_start, log_analysis_complete, log_site_analyzed

client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)

@retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
def analyze_site(site):
    """Use Claude to analyze a deal site and write rationale"""
    ctx = f"""Search result title: {site.get('title', '')}
URL: {site.get('link', '')}
Snippet: {site.get('snippet', '')}

From live page scrape:
- Deal title (on-page): {site.get('deal_title', '') or '(none)'}
- Price signal: {site.get('deal_price', '') or '(none)'}
- Promo / body excerpt: {site.get('promo_copy', '')[:1200] or '(none)'}
- Meh-vibe keyword hits: {site.get('meh_signals', '') or '(none)'}
- Screenshot (local path for your context only): {site.get('screenshot_path', '') or '(none)'}
- Scrape method: {site.get('scrape_method', '')}"""

    prompt = f"""You are vetting candidates for someone hunting indie "one deal a day" / Meh-style sites.

{ctx}

CRITICAL DISQUALIFIERS — score 0 immediately if ANY apply:
- Article, blog post, or listicle ABOUT deal sites — not itself a deal site
- News outlet, tech blog, or review site (any major media domain)
- Restaurant, food delivery, bar, or café running a daily food special
- Florist, gift shop, or local service business with a "deal of the day"
- Cannabis dispensary or local retail store
- Affiliate blog promoting another store's deals (e.g. "Today only at Sephora…")
- Single product page on a general e-commerce or seafood/food delivery site
- Community forum, social media post, or user discussion thread
- Real estate, financial services, or B2B company
- Non-US site (South Africa, Portugal, Indonesia, Australia, UK, India, etc.)
- Large marketplace (Amazon, eBay, Walmart, etc.)
- Coupon or promo-code aggregator

ONLY score ≥6 if: a US-based, independently operated website whose PRIMARY business model is selling ONE discounted product per day directly to consumers.

Score rubric:
  0-2  Not a deal site at all (articles about deals, blogs, news, local shops, B2B)
  3-4  Deal-adjacent but wrong format (multi-item, big marketplace, coupon aggregator)
  5-6  Has deals but lacks focus, personality, or clear single-item format
  7-8  Solid single-item daily deal site with some personality
  9-10 Textbook Meh clone — one item, witty copy, strong deal, clear pricing

Be concise in rationale (one sentence max). Return JSON only:
{{"rationale": "...", "quality_score": <int 0-10>, "niche": "..."}}"""

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=220,
            temperature=0.55,
            timeout=30.0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        log_site_analyzed(site['link'], 0, success=False, error=str(e))
        return f"Error analyzing: {str(e)}"

def analyze_sites_batch(sites, max_workers=10):
    """Analyze multiple sites in parallel"""
    log_analysis_start(len(sites))
    results = []
    errors = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_site = {executor.submit(analyze_site, site): site for site in sites}
        for future in as_completed(future_to_site):
            site = future_to_site[future]
            try:
                analysis = future.result()
                results.append({'site': site, 'analysis': analysis})
            except Exception as e:
                errors += 1
                results.append({'site': site, 'analysis': f"Error: {str(e)}"})
    log_analysis_complete(len(results), errors)
    return results

# Quick test
if __name__ == "__main__":
    from scraper import search_for_deal_sites
    results = search_for_deal_sites()
    if results:
        logger.info("test_analysis", message="Analyzing first site...")
        analysis = analyze_site(results[0])
        logger.info("test_analysis_result", result=analysis)