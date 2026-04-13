"""
No-op stub for candidate guardrail checks.

Real implementation is intended to inspect a candidate dict and return a list
of flag strings (e.g. suspicious TLDs, blocked keywords) plus a rejection
reason derived from those flags. This stub returns nothing so the scraper
pipeline treats every candidate as passing guardrails.

Call sites in scraper.py:
  - detect_candidate_guardrail_flags(candidate) -> list[str]
  - candidate_guardrail_rejection_reason(candidate, flags) -> str | None
"""
from __future__ import annotations


def detect_candidate_guardrail_flags(candidate: dict) -> list[str]:
    """Return a list of guardrail flag strings for the candidate. Stub: no flags."""
    return []


def candidate_guardrail_rejection_reason(
    candidate: dict, flags: list[str]
) -> str | None:
    """Return a rejection reason string if the flags warrant rejection. Stub: never rejects."""
    return None
