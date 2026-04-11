"""
How candidates are scored (exported into candidates.json under "scoring" and used for CSV headers).

Two layers:
1) Supplier / discovery (before visit): vibe_score on Serper title+snippet.
2) Supplier + product context (after page scrape): Claude quality_score using on-page signals;
   page keyword hits recorded as meh_signals / meh_signal_hits.
"""

from __future__ import annotations

import json

# Human-readable methodology for dashboards and JSON exports.
CANDIDATE_SCORING_REFERENCE = {
    "discovery_source": {
        "meaning": "Where the candidate came from before enrichment: 'search' for live Serper discovery or 'seed' for the curated known-good list.",
    },
    "vibe_score": {
        "range": "0–10 (integer)",
        "where": "scraper.score_meh_vibe(search_result_title, search_result_snippet)",
        "meaning": (
            "Cheap pre-visit heuristic: +1 per positive token (deal, daily, one, flash, …), "
            "-2 per negative token (amazon, marketplace, coupon codes, …), +2 if the title "
            "matches deal|sale|meh|daily|steal|score, −1 for generic Shopify/Etsy hosts, "
            "then clamped to 0–10."
        ),
        "discovery_gate": "Serper results are kept only when vibe_score >= 4. "
        "Curated seed URLs from config get vibe_score 7 by default.",
    },
    "meh_signals": {
        "where": "scraper._collect_meh_signals on page title, promo/body text, price line, "
        "and optional accessibility hints",
        "meaning": (
            "Comma-separated hits from MEH_SIGNAL_KEYWORDS (e.g. one deal, daily deal, "
            "today only, flash sale). Describes on-page copy, not a second numeric grade."
        ),
    },
    "meh_signal_hits": {
        "meaning": "Count of distinct keyword phrases listed in meh_signals (0 if none).",
    },
    "quality_score": {
        "range": "0–10 (integer from model JSON)",
        "where": "analyzer.analyze_site — Anthropic Claude (model in analyzer.py)",
        "meaning": (
            "Whether the URL is a US-facing indie “one discounted item per day” merchant "
            "(vs blog, marketplace, news, local restaurant deal, etc.). See analyzer prompt "
            "for disqualifiers and rubric."
        ),
        "acceptance_gate": "A candidate is “accepted” for the sheet/dashboard when "
        "quality_score >= 6 (same threshold as main.py deal filter).",
    },
    "rationale": {
        "meaning": "One-sentence model explanation for the quality_score (supplier judgment).",
    },
    "product_fields": {
        "deal_title": "On-page title (Playwright h1 / og:title / soup) — display only, not a separate numeric score.",
        "deal_price": "First price-like string found on page (semantic tags + regex).",
        "original_price": "Strikethrough / compare-at / class patterns when present.",
        "note": "These support the LLM and your review; they are not additional graded scores.",
    },
}


def build_candidate_records(analyses: list[dict]) -> list[dict]:
    """
    One row per analyzed URL: discovery scores, scrape fields, and LLM supplier score.
    """
    out: list[dict] = []
    for item in analyses:
        site = item["site"]
        raw = item["analysis"]
        ms = (site.get("meh_signals") or "").strip()
        hits = len([x for x in ms.split(",") if x.strip()]) if ms else 0
        common = {
            "site_name": site.get("title", ""),
            "url": site.get("link", ""),
            "snippet": site.get("snippet", "") or "",
            "discovery_source": site.get("discovery_source", "") or "",
            "source_status": site.get("source_status", "") or "",
            "vibe_score": site.get("vibe_score", ""),
            "deal_title": site.get("deal_title", ""),
            "deal_price": site.get("deal_price", ""),
            "original_price": site.get("original_price", ""),
            "meh_signals": ms,
            "meh_signal_hits": hits,
            "scrape_method": site.get("scrape_method", "") or "",
        }
        try:
            parsed = json.loads(raw)
            score = parsed.get("quality_score", 0)
            accepted = score >= 6
            out.append(
                {
                    **common,
                    "quality_score": score,
                    "niche": parsed.get("niche", ""),
                    "rationale": (parsed.get("rationale") or "")[:4000],
                    "accepted": accepted,
                    "rejection_reason": "" if accepted else f"score {score} < 6",
                }
            )
        except Exception:
            out.append(
                {
                    **common,
                    "quality_score": None,
                    "niche": "",
                    "rationale": "",
                    "accepted": False,
                    "rejection_reason": "analysis parse error",
                }
            )
    return out
