import requests
import pytest
from tenacity import wait_none
from bs4 import BeautifulSoup

import scraper


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


# ──────────────────────────────────────────────────────────────────────────────
# Helpers for new product quality functions
# ──────────────────────────────────────────────────────────────────────────────

def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── _extract_json_ld_product ──────────────────────────────────────────────────

def test_extract_json_ld_product_returns_first_product():
    html = """<html><head>
    <script type="application/ld+json">{"@type": "Product", "name": "Widget", "brand": {"name": "Acme"}}</script>
    </head></html>"""
    ld = scraper._extract_json_ld_product(make_soup(html))
    assert ld.get("name") == "Widget"
    assert ld.get("brand", {}).get("name") == "Acme"


def test_extract_json_ld_product_handles_list_wrapper():
    html = """<html><head>
    <script type="application/ld+json">[{"@type": "WebSite", "name": "Shop"},
    {"@type": "Product", "name": "Gadget"}]</script>
    </head></html>"""
    ld = scraper._extract_json_ld_product(make_soup(html))
    assert ld.get("name") == "Gadget"


def test_extract_json_ld_product_handles_graph():
    html = """<html><head>
    <script type="application/ld+json">{"@graph": [{"@type": "WebPage"}, {"@type": "Product", "name": "GraphItem"}]}</script>
    </head></html>"""
    ld = scraper._extract_json_ld_product(make_soup(html))
    assert ld.get("name") == "GraphItem"


def test_extract_json_ld_product_returns_empty_when_no_product():
    html = """<html><head>
    <script type="application/ld+json">{"@type": "WebSite", "name": "Shop"}</script>
    </head></html>"""
    ld = scraper._extract_json_ld_product(make_soup(html))
    assert ld == {}


def test_extract_json_ld_product_handles_invalid_json():
    html = """<html><head>
    <script type="application/ld+json">THIS IS NOT JSON</script>
    </head></html>"""
    ld = scraper._extract_json_ld_product(make_soup(html))
    assert ld == {}


# ── _compute_completeness ────────────────────────────────────────────────────

def test_compute_completeness_all_fields():
    rec = {"deal_title": "Shoe", "deal_price": "$30", "image_url": "http://img", "brand": "Nike"}
    assert scraper._compute_completeness(rec) == 1.0


def test_compute_completeness_half_fields():
    rec = {"deal_title": "Shoe", "deal_price": "$30", "image_url": "", "brand": ""}
    assert scraper._compute_completeness(rec) == 0.5


def test_compute_completeness_no_fields():
    rec = {"deal_title": "", "deal_price": "", "image_url": "", "brand": ""}
    assert scraper._compute_completeness(rec) == 0.0


def test_compute_completeness_ignores_whitespace_only():
    rec = {"deal_title": "   ", "deal_price": "$10", "image_url": "", "brand": ""}
    assert scraper._compute_completeness(rec) == pytest.approx(0.25)


# ── _compute_canonical_key ───────────────────────────────────────────────────

def test_compute_canonical_key_gtin_takes_priority():
    rec = {"gtin": "012345678901", "brand": "Acme", "deal_title": "Widget"}
    key = scraper._compute_canonical_key(rec)
    assert key == "gtin:012345678901"


def test_compute_canonical_key_brand_title_fallback():
    rec = {"gtin": "", "brand": "Nike", "deal_title": "Air Max"}
    key = scraper._compute_canonical_key(rec)
    assert key.startswith("bt:")
    assert len(key) > 3


def test_compute_canonical_key_title_only_fallback():
    rec = {"gtin": "", "brand": "", "deal_title": "Mystery Product"}
    key = scraper._compute_canonical_key(rec)
    assert key.startswith("t:")


def test_compute_canonical_key_empty_returns_empty():
    rec = {"gtin": "", "brand": "", "deal_title": ""}
    assert scraper._compute_canonical_key(rec) == ""


def test_compute_canonical_key_same_inputs_produce_same_key():
    rec1 = {"gtin": "", "brand": "Acme", "deal_title": "Widget Pro"}
    rec2 = {"gtin": "", "brand": "ACME", "deal_title": "Widget  Pro"}
    # Normalization (lowercase + whitespace collapse) makes these equal
    assert scraper._compute_canonical_key(rec1) == scraper._compute_canonical_key(rec2)


# ── _extract_from_soup: structured-data-first ────────────────────────────────

def test_extract_from_soup_uses_json_ld_for_price():
    html = """<html><head>
    <script type="application/ld+json">{"@type":"Product","name":"Dealiator",
    "offers":{"@type":"Offer","price":"29.99","priceCurrency":"USD"}}</script>
    <title>Dealiator</title></head><body><h1>Dealiator</h1></body></html>"""
    result = scraper._extract_from_soup(make_soup(html), "https://example.com/")
    assert "29.99" in result["deal_price"]
    assert result["deal_title"] == "Dealiator"


def test_extract_from_soup_extracts_brand():
    html = """<html><head>
    <script type="application/ld+json">{"@type":"Product","name":"Shoe",
    "brand":{"@type":"Brand","name":"Adidas"}}</script>
    </head><body><h1>Shoe</h1></body></html>"""
    result = scraper._extract_from_soup(make_soup(html), "https://example.com/")
    assert result["brand"] == "Adidas"


def test_extract_from_soup_extracts_image():
    html = """<html><head>
    <meta property="og:image" content="https://img.example.com/shoe.jpg"/>
    <title>Shoe deal</title></head><body></body></html>"""
    result = scraper._extract_from_soup(make_soup(html), "https://example.com/")
    assert result["image_url"] == "https://img.example.com/shoe.jpg"


def test_extract_from_soup_returns_completeness_and_canonical_key():
    html = """<html><head>
    <script type="application/ld+json">{"@type":"Product","name":"Thing",
    "offers":{"price":"9.99","priceCurrency":"USD"},
    "brand":{"name":"Makers"},"image":"https://img/t.jpg"}</script>
    </head><body><h1>Thing</h1></body></html>"""
    result = scraper._extract_from_soup(make_soup(html), "https://example.com/")
    assert result["completeness_score"] == 1.0
    assert result["canonical_key"].startswith("bt:")


# ── _is_blocked_domain: new domains ─────────────────────────────────────────

def test_is_blocked_domain_new_spam_sites():
    for domain_url in [
        "https://dealnews.com/product",
        "https://retailmenot.com/view/deal",
        "https://slickdeals.net/deals",
        "https://wirecutter.com/reviews",
        "https://consumerreports.org/test",
    ]:
        assert scraper._is_blocked_domain(domain_url), f"Expected {domain_url} to be blocked"


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
