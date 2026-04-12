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
                        "link": "https://blog.example.com/post",
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


# ── Canonical product URL extraction tests ───────────────────────────────────

from bs4 import BeautifulSoup


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_extract_canonical_url_from_jsonld_product_url():
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","url":"https://example.com/products/widget-pro","name":"Widget Pro"}
    </script></head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/products/widget-pro"


def test_extract_canonical_url_from_jsonld_offers_url():
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Gizmo",
     "offers":{"@type":"Offer","url":"https://example.com/products/gizmo","price":"9.99"}}
    </script></head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/products/gizmo"


def test_extract_canonical_url_from_jsonld_offers_list():
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Gadget",
     "offers":[{"@type":"Offer","url":"https://shop.example.com/gadget","price":"19.99"}]}
    </script></head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://shop.example.com/gadget"


def test_extract_canonical_url_from_jsonld_graph():
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"WebSite","name":"Shop"},
      {"@type":"Product","url":"https://example.com/p/item-42","name":"Item 42"}
    ]}
    </script></head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/p/item-42"


def test_extract_canonical_url_from_canonical_tag():
    html = """<html><head>
    <link rel="canonical" href="https://example.com/products/blue-widget"/>
    </head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/products/blue-widget"


def test_extract_canonical_url_from_microdata():
    html = """<html><body>
    <div itemscope itemtype="https://schema.org/Product">
      <link itemprop="url" href="https://example.com/item/microdata-product"/>
    </div>
    </body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/item/microdata-product"


def test_extract_canonical_url_skips_root_canonical():
    """A canonical tag pointing to the homepage should not be used."""
    html = """<html><head>
    <link rel="canonical" href="https://example.com/"/>
    </head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == ""


def test_extract_canonical_url_prefers_jsonld_over_canonical_tag():
    """JSON-LD product URL takes precedence over the canonical tag."""
    html = """<html><head>
    <link rel="canonical" href="https://example.com/products/fallback"/>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","url":"https://example.com/products/preferred","name":"Best"}
    </script></head><body></body></html>"""
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/")
    assert result == "https://example.com/products/preferred"


def test_extract_canonical_url_returns_empty_when_nothing_found():
    """With no structured data at all, return empty string so caller can warn."""
    html = "<html><head></head><body><h1>Some page</h1></body></html>"
    soup = _make_soup(html)
    result = scraper._extract_canonical_product_url(soup, "https://example.com/some-page")
    assert result == ""


def test_extract_from_soup_uses_product_url_from_jsonld(monkeypatch):
    """_extract_from_soup should populate product_url from JSON-LD."""
    html = """<html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","url":"https://example.com/p/fancy-item","name":"Fancy Item"}
    </script></head><body><h1>Fancy Item</h1></body></html>"""
    soup = _make_soup(html)
    data = scraper._extract_from_soup(soup, "https://example.com/")
    assert data["product_url"] == "https://example.com/p/fancy-item"


def test_extract_from_soup_falls_back_to_crawl_url_without_warning_for_non_root(monkeypatch):
    """When no structured URL is found and crawl URL has a path, use crawl URL (no home-page warning)."""
    warnings = []
    monkeypatch.setattr(
        scraper.logger,
        "warning",
        lambda event, *args, **kwargs: warnings.append(event),
    )
    html = "<html><head></head><body><h1>Deal</h1></body></html>"
    soup = _make_soup(html)
    data = scraper._extract_from_soup(soup, "https://example.com/deals/todays-deal")
    assert data["product_url"] == "https://example.com/deals/todays-deal"
    assert "product_url_fallback_homepage" not in warnings


def test_extract_from_soup_warns_when_crawl_url_is_homepage(monkeypatch):
    """When no structured URL is found and crawl URL is a homepage, a warning is logged."""
    warnings = []
    monkeypatch.setattr(
        scraper.logger,
        "warning",
        lambda event, *args, **kwargs: warnings.append(event),
    )
    html = "<html><head></head><body><h1>Deal</h1></body></html>"
    soup = _make_soup(html)
    data = scraper._extract_from_soup(soup, "https://example.com/")
    assert data["product_url"] == "https://example.com/"
    assert "product_url_fallback_homepage" in warnings


def test_is_root_url():
    assert scraper._is_root_url("https://example.com/") is True
    assert scraper._is_root_url("https://example.com") is True
    assert scraper._is_root_url("https://example.com/products/item") is False
    assert scraper._is_root_url("https://example.com/p/42") is False
