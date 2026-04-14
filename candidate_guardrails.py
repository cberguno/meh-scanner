"""
URL-shape and hostname guardrails for search candidates.

Runs BEFORE Playwright enrich / Anthropic analysis so obviously-not-a-deal-site
results are dropped early. Counts toward per-query drop_reasons as
``guardrail_<flag>``.

Flags emitted (in priority order — first flag drives the rejection reason):
  - ``forum_host``      : hostname starts with forums./community./support./help./docs./discourse./dev./developer.
  - ``forum_path``      : path matches /forum(s)?, /threads?/, /topics?/, /discussion/, /node/, /t/<n>, /c/<n>
  - ``support_page``    : path matches /faq, /faqs, /privacy, /tos, /terms, /about, /contact, /support, /help
  - ``article_path``    : path matches /blog/, /blogs/, /news/, /press/, /article/, /articles/, /posts/, /post/,
                          or ends in /<date>/<slug> (YYYY/MM/DD) — almost always a blog post
  - ``social_profile``  : path is a social profile (e.g. /@handle, or lemon8/tiktok-style @handle/<numeric id>)

Non-rejecting (informational) flags:
  - ``long_slug``       : path has a hyphenated slug with ≥8 tokens (noted, but not auto-rejected
                          because deal sites legitimately ship long product slugs)

Design notes
  - Only inspects ``candidate["link"]`` — no network calls.
  - Seeds are never rejected (scraper calls ``detect_...`` but ignores the
    reason for seed entries); we still surface flags so they show up in the
    dashboard.
  - Keep this list TIGHT. Over-blocking here silently kills discovery.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Hostnames that are almost never one-deal-a-day sites even when the domain is new
_FORUM_HOST_PREFIXES = (
    "forums.",
    "forum.",
    "community.",
    "communities.",
    "support.",
    "help.",
    "docs.",
    "discourse.",
    "dev.",
    "developer.",
    "developers.",
    "status.",
)

# Path prefixes that identify forum / thread / discussion surfaces.
_FORUM_PATH_PATTERNS = (
    re.compile(r"/forums?(/|$)"),
    re.compile(r"/threads?(/|$)"),
    re.compile(r"/topics?(/|$)"),
    re.compile(r"/discussions?(/|$)"),
    re.compile(r"/node/\d"),
    re.compile(r"/t/\d"),
    re.compile(r"/c/\d"),
    re.compile(r"/viewtopic\.php"),
    re.compile(r"/showthread\.php"),
)

# Support / policy pages that will never be a deal page.
_SUPPORT_PATH_PATTERNS = (
    re.compile(r"/faqs?(/|$)"),
    re.compile(r"/privacy(-policy)?(/|$)"),
    re.compile(r"/terms(-of-(service|use))?(/|$)"),
    re.compile(r"/tos(/|$)"),
    re.compile(r"/legal(/|$)"),
    re.compile(r"/about(-us)?(/|$)"),
    re.compile(r"/contact(-us)?(/|$)"),
    re.compile(r"/support(/|$)"),
    re.compile(r"/help(/|$)"),
    re.compile(r"/shipping(/|$)"),
    re.compile(r"/returns?(/|$)"),
)

# Blog / news / press surfaces.
_ARTICLE_PATH_PATTERNS = (
    re.compile(r"/blogs?(/|$)"),
    re.compile(r"/news(/|$)"),
    re.compile(r"/press(/|$)"),
    re.compile(r"/articles?(/|$)"),
    re.compile(r"/posts?(/|$)"),
    re.compile(r"/stories(/|$)"),
    re.compile(r"/magazine(/|$)"),
    # Date-prefixed permalinks: /2024/08/15/slug, /2024-08-15/slug, etc.
    re.compile(r"/(19|20)\d{2}/[01]?\d/[0-3]?\d/"),
    re.compile(r"/(19|20)\d{2}-[01]?\d-[0-3]?\d/"),
    # Bare-year archive: /2024/slug (rare but strong blog signal)
    re.compile(r"/(19|20)\d{2}/[a-z]"),
)

# Social-media profile surfaces (lemon8, tiktok-style). Catches things like
# /@handle/7550108054085485070 and plain /@handle.
_SOCIAL_PROFILE_PATTERNS = (
    re.compile(r"/@[A-Za-z0-9_.-]+(/|$)"),
)


def _normalize_host_path(url: str) -> tuple[str, str]:
    """Return (host, path) with host lowercased, ``www.`` stripped, path trailing-slash preserved."""
    if not url:
        return "", ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    return host, path


def _has_long_hyphen_slug(path: str) -> bool:
    """True when any single path segment is a hyphenated slug with ≥8 tokens."""
    for segment in path.split("/"):
        if "-" not in segment:
            continue
        tokens = [t for t in segment.split("-") if t]
        if len(tokens) >= 8:
            return True
    return False


def detect_candidate_guardrail_flags(candidate: dict) -> list[str]:
    """Return a list of guardrail flag strings for the candidate.

    Flags are ordered by priority: the first flag drives the rejection reason.
    """
    url = str(candidate.get("link") or candidate.get("url") or "")
    if not url:
        return []

    host, path = _normalize_host_path(url)
    flags: list[str] = []

    # ── Forum host: highest priority — entire site is forum/community ────────
    if any(host.startswith(prefix) for prefix in _FORUM_HOST_PREFIXES):
        flags.append("forum_host")

    # ── Forum / thread / discussion path ─────────────────────────────────────
    if any(p.search(path) for p in _FORUM_PATH_PATTERNS):
        flags.append("forum_path")

    # ── Support / policy page ────────────────────────────────────────────────
    if any(p.search(path) for p in _SUPPORT_PATH_PATTERNS):
        flags.append("support_page")

    # ── Blog / news / press / dated permalink ────────────────────────────────
    if any(p.search(path) for p in _ARTICLE_PATH_PATTERNS):
        flags.append("article_path")

    # ── Social profile (lemon8, tiktok-style) ────────────────────────────────
    if any(p.search(path) for p in _SOCIAL_PROFILE_PATTERNS):
        flags.append("social_profile")

    # ── Long hyphenated slug: informational only, NOT auto-rejected ──────────
    # Daily-deal sites legitimately use long product slugs. We annotate so the
    # dashboard can surface the signal but we don't drop on it alone.
    if _has_long_hyphen_slug(path):
        flags.append("long_slug")

    return flags


# Flags that cause the candidate to be rejected outright before LLM analysis.
_REJECTING_FLAGS = frozenset({
    "forum_host",
    "forum_path",
    "support_page",
    "article_path",
    "social_profile",
})


def candidate_guardrail_rejection_reason(
    candidate: dict, flags: list[str]
) -> str | None:
    """Return a rejection reason string if any flag is in the rejecting set."""
    for flag in flags or ():
        if flag in _REJECTING_FLAGS:
            return flag
    return None
