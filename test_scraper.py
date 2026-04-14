import requests
import pytest
from tenacity import wait_none

import scraper


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def configure_search_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    queries=None,
    seeds=None,
    statuses=None,
    seen_urls=None,
    max_candidates: int = 25,
):
    seen = {scraper.normalize_url(url) for url in (seen_urls or set())}
    statuses = dict(statuses or {})
    marked = []

    monkeypatch.setattr(scraper, "init_db", lambda: None)
    monkeypatch.setattr(scraper, "log_search_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(scraper, "log_search_complete", lambda *args, **kwargs: None)
    monkeypatch.setattr(scraper, "log_site_scraped", lambda *args, **kwargs: None)
    monkeypatch.setattr(scraper, "wait_exponential", lambda **kwargs: wait_none())
    monkeypatch.setattr(scraper.Config, "SERPER_API_KEY", "test-serper-key")
    monkeypatch.setattr(scraper.Config, "SEARCH_QUERIES", list(queries or []))
    monkeypatch.setattr(scraper.Config, "SEED_DEAL_SITES", list(seeds or []))
    monkeypatch.setattr(scraper.Config, "MAX_CANDIDATES_PER_RUN", max_candidates)
    monkeypatch.setattr(scraper.Config, "SEARCH_RESULTS_PER_QUERY", 10)
    monkeypatch.setattr(scraper.Config, "SEARCH_VIBE_THRESHOLD", 4)
    monkeypatch.setattr(scraper.Config, "SEARCH_FALLBACK_VIBE_THRESHOLD", 3)
    monkeypatch.setattr(scraper.Config, "SEARCH_MIN_LIVE_CANDIDATES", 3)
    monkeypatch.setattr(scraper.Config, "SEARCH_REJECTION_SAMPLE_LIMIT", 8)
    monkeypatch.setattr(scraper, "_LAST_SEARCH_DIAGNOSTICS", {})
    monkeypatch.setattr(scraper, "is_site_seen", lambda url: scraper.normalize_url(url) in seen)
    monkeypatch.setattr(scraper, "mark_site_seen", lambda url: marked.append(url))
    monkeypatch.setattr(scraper, "get_source_status", lambda domain: statuses.get(domain, "new"))
    return marked


def make_candidate_response(link: str) -> DummyResponse:
    return DummyResponse(
        200,
        {
            "organic": [
                {
                    "title": "One Deal a Day",
                    "link": link,
                    "snippet": "Today only while supplies last limited time",
                }
            ]
        },
    )


def test_normalize_url_removes_query_before_trailing_slash():
    assert (
        scraper.normalize_url("https://Example.com/path/?a=1#frag")
        == "https://example.com/path"
    )


def test_search_retries_request_failures(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["retry-me"])
    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.exceptions.Timeout("temporary timeout")
        return make_candidate_response("https://fresh.example.com/deal?a=1")

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert attempts["count"] == 3
    assert [r["link"] for r in results] == ["https://fresh.example.com/deal?a=1"]
    assert results[0]["discovery_source"] == "search"
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["queries_succeeded"] == 1
    assert diagnostics["queries_failed"] == 0
    assert diagnostics["selected_search_candidates"] == 1


def test_search_continues_after_exhausted_query_retries(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["broken", "healthy"])
    attempts = {"broken": 0, "healthy": 0}

    def fake_post(*args, **kwargs):
        query = kwargs["json"]["q"]
        attempts[query] += 1
        if query == "broken":
            raise requests.exceptions.Timeout("still timing out")
        return make_candidate_response("https://healthy.example.com/deal")

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert attempts["broken"] == 3
    assert attempts["healthy"] == 1
    assert [r["link"] for r in results] == ["https://healthy.example.com/deal"]
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["queries_failed"] == 1
    assert diagnostics["selected_search_candidates"] == 1
    assert diagnostics["degraded_search"] is False


def test_seed_sites_are_included_even_when_previously_seen(monkeypatch: pytest.MonkeyPatch):
    marked = configure_search_env(
        monkeypatch,
        queries=[],
        seeds=[
            {
                "title": "Seed",
                "link": "https://seed.example.com",
                "snippet": "One deal a day",
            }
        ],
        seen_urls={"https://seed.example.com"},
    )

    results = scraper.search_for_deal_sites()

    assert [r["link"] for r in results] == ["https://seed.example.com"]
    assert marked == []
    assert results[0]["discovery_source"] == "seed"


def test_search_marks_candidates_only_after_downstream_processing(monkeypatch: pytest.MonkeyPatch):
    marked = configure_search_env(monkeypatch)

    marked_count = scraper.mark_candidates_seen(
        [
            {"link": "https://alpha.example.com/deal?a=1"},
            {"url": "https://alpha.example.com/deal"},
            {"link": "https://beta.example.com/deal"},
            {},
        ]
    )

    assert marked_count == 2
    assert marked == [
        "https://alpha.example.com/deal?a=1",
        "https://beta.example.com/deal",
    ]


def test_force_domains_accept_full_urls_for_remove_override(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(
        monkeypatch,
        queries=["force-me"],
        statuses={"keepme.example.com": "remove"},
    )

    def fake_post(*args, **kwargs):
        return make_candidate_response("https://www.keepme.example.com/deal")

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites(
        force_domains=frozenset({"https://www.keepme.example.com/path"})
    )

    assert [r["link"] for r in results] == ["https://www.keepme.example.com/deal"]
    assert results[0]["source_status"] == "remove"
    assert results[0]["force_included"] is True


def test_search_deduplicates_normalized_urls(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["dedupe"])

    def fake_post(*args, **kwargs):
        return DummyResponse(
            200,
            {
                "organic": [
                    {
                        "title": "One Deal a Day",
                        "link": "https://dupe.example.com/path/?a=1",
                        "snippet": "Today only while supplies last limited time",
                    },
                    {
                        "title": "One Deal a Day",
                        "link": "https://dupe.example.com/path",
                        "snippet": "Today only while supplies last limited time",
                    },
                ]
            },
        )

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert len(results) == 1
    assert scraper.normalize_url(results[0]["link"]) == "https://dupe.example.com/path"
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["drop_reasons"]["duplicate_url"] == 1


def test_search_flags_degraded_seed_only_mode(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(
        monkeypatch,
        queries=["broken"],
        seeds=[
            {
                "title": "Seed",
                "link": "https://seed.example.com",
                "snippet": "One deal a day",
            }
        ],
    )

    def fake_post(*args, **kwargs):
        raise requests.exceptions.Timeout("still timing out")

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert [r["discovery_source"] for r in results] == ["seed"]
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["queries_failed"] == 1
    assert diagnostics["selected_search_candidates"] == 0
    assert diagnostics["selected_seed_candidates"] == 1
    assert diagnostics["degraded_search"] is True


def test_search_relaxes_threshold_when_strict_yield_is_too_low(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["adaptive"], seeds=[])
    monkeypatch.setattr(scraper.Config, "SEARCH_MIN_LIVE_CANDIDATES", 1)
    monkeypatch.setattr(scraper, "score_meh_vibe", lambda title, snippet: 3)

    def fake_post(*args, **kwargs):
        return make_candidate_response("https://borderline.example.com/deal")

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert [r["link"] for r in results] == ["https://borderline.example.com/deal"]
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["relaxed_vibe_threshold_used"] is True
    assert diagnostics["promoted_borderline_candidates"] == 1
    assert diagnostics["selected_search_candidates"] == 1


def test_search_keeps_rejection_samples_in_diagnostics(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["samples"], seeds=[])
    monkeypatch.setattr(scraper, "score_meh_vibe", lambda title, snippet: 1)

    def fake_post(*args, **kwargs):
        return DummyResponse(
            200,
            {
                "organic": [
                    {
                        "title": "List of deal sites",
                        # Use a clean path that passes guardrails so the only
                        # rejection reason is the forced low vibe score.
                        "link": "https://example.com/somewhere",
                        "snippet": "An article about the best deal sites",
                    }
                ]
            },
        )

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert results == []
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["rejection_samples"]
    assert diagnostics["rejection_samples"][0]["reason"] == "low_vibe"


# ── guardrail tests ──────────────────────────────────────────────────────────

import candidate_guardrails as cg


def test_guardrail_flags_forum_host_and_path():
    flags = cg.detect_candidate_guardrail_flags(
        {"link": "https://forums.spiralknights.com/en/node/124775"}
    )
    assert "forum_host" in flags
    assert "forum_path" in flags
    assert cg.candidate_guardrail_rejection_reason({}, flags) == "forum_host"


def test_guardrail_flags_support_page():
    flags = cg.detect_candidate_guardrail_flags(
        {"link": "https://peachyplannerdeals.com/faq/"}
    )
    assert flags == ["support_page"]
    assert cg.candidate_guardrail_rejection_reason({}, flags) == "support_page"


def test_guardrail_flags_social_profile():
    flags = cg.detect_candidate_guardrail_flags(
        {
            "link": "https://www.lemon8-app.com/@artistry_by_ashlee/7471406069572010542?region=us"
        }
    )
    assert "social_profile" in flags
    assert cg.candidate_guardrail_rejection_reason({}, flags) == "social_profile"


def test_guardrail_flags_article_path_dated_permalink():
    flags = cg.detect_candidate_guardrail_flags(
        {"link": "https://example.com/2024/08/15/cool-post"}
    )
    assert "article_path" in flags
    assert cg.candidate_guardrail_rejection_reason({}, flags) == "article_path"


def test_guardrail_flags_article_path_blog_prefix():
    flags = cg.detect_candidate_guardrail_flags(
        {"link": "https://store.example.com/blogs/news/some-post"}
    )
    assert "article_path" in flags


def test_guardrail_long_slug_is_informational_only():
    # A product slug with 8+ hyphen tokens should flag but NOT be rejected.
    flags = cg.detect_candidate_guardrail_flags(
        {
            "link": "https://www.thatdailydeal.com/moon-lamp-with-16-color-options-and-remote-control-ships-free"
        }
    )
    assert flags == ["long_slug"]
    assert cg.candidate_guardrail_rejection_reason({}, flags) is None


def test_guardrail_homepage_is_clean():
    assert cg.detect_candidate_guardrail_flags({"link": "https://meh.com"}) == []
    assert cg.detect_candidate_guardrail_flags({"link": "https://meh.com/"}) == []


def test_guardrail_product_path_is_clean():
    # Classic daily-deal product path shape — must NOT trigger any guardrail.
    assert (
        cg.detect_candidate_guardrail_flags(
            {"link": "https://www.yugster.com/deal/12345"}
        )
        == []
    )


def test_search_drops_forum_candidate_via_guardrail(
    monkeypatch: pytest.MonkeyPatch,
):
    configure_search_env(monkeypatch, queries=["forum-junk"])

    def fake_post(*args, **kwargs):
        return DummyResponse(
            200,
            {
                "organic": [
                    {
                        "title": "One Deal a Day",
                        "link": "https://forums.example.com/threads/42",
                        "snippet": "today only limited time",
                    }
                ]
            },
        )

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert results == []
    diagnostics = scraper.get_last_search_diagnostics()
    # drop_reason code is ``guardrail_<reason>`` — reason is the first rejecting flag
    assert any(
        code.startswith("guardrail_forum")
        for code in diagnostics["drop_reasons"].keys()
    )


def test_seed_injection_prunes_same_domain_search_results(
    monkeypatch: pytest.MonkeyPatch,
):
    # Live search returns a deeper path on the same domain as a seed homepage.
    # Expected: seed wins, search result is dropped via seed_domain_conflict.
    configure_search_env(
        monkeypatch,
        queries=["dupe"],
        seeds=[
            {
                "title": "13 Deals",
                "link": "https://www.13deals.com",
                "snippet": "Daily deals flash sale",
            }
        ],
    )

    def fake_post(*args, **kwargs):
        return make_candidate_response(
            "https://www.13deals.com/store/categories/105-seasonal"
        )

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    # Only the seed homepage remains, not the deeper search result.
    assert [r["link"] for r in results] == ["https://www.13deals.com"]
    assert results[0]["discovery_source"] == "seed"
    diagnostics = scraper.get_last_search_diagnostics()
    assert diagnostics["drop_reasons"].get("seed_domain_conflict") == 1


def test_lemon8_domain_is_blocked_before_scoring(monkeypatch: pytest.MonkeyPatch):
    configure_search_env(monkeypatch, queries=["social-junk"])

    def fake_post(*args, **kwargs):
        return DummyResponse(
            200,
            {
                "organic": [
                    {
                        "title": "Flash Sale",
                        "link": "https://www.lemon8-app.com/@handle/123",
                        "snippet": "flash sale 25 off today only",
                    }
                ]
            },
        )

    monkeypatch.setattr(scraper.requests, "post", fake_post)

    results = scraper.search_for_deal_sites()

    assert results == []
    diagnostics = scraper.get_last_search_diagnostics()
    # Domain block runs BEFORE guardrails/scoring — drop reason is blocked_domain
    assert diagnostics["drop_reasons"].get("blocked_domain") == 1
