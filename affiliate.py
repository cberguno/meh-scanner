"""
Affiliate URL builder.
Currently supports Amazon Associates.
Add new programs by extending _build() only — callers never change.
"""
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from config import Config


def apply_affiliate_url(deal: dict) -> dict:
    """
    Return deal dict with 'affiliate_url' key added.
    Value is the affiliate-tagged URL when a program applies, '' otherwise.
    Existing keys are preserved untouched.
    """
    return {**deal, "affiliate_url": _build(deal.get("url", ""))}


# ── private helpers ───────────────────────────────────────────────────────────

def _build(url: str) -> str:
    """Return an affiliate-tagged URL, or '' if no program matches."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        return ""

    if Config.AMAZON_AFFILIATE_TAG and "amazon." in host:
        return _amazon(url, Config.AMAZON_AFFILIATE_TAG)

    return ""


def _amazon(url: str, tag: str) -> str:
    """Append or replace the Amazon Associates tag parameter."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["tag"] = [tag]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))
