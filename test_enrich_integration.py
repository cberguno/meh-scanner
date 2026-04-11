import sys
import types
import os

import scraper


def make_fake_playwright_module(html):
    mod = types.ModuleType("playwright.sync_api")
    # Provide a TimeoutError for imports in extractor
    mod.TimeoutError = Exception

    class FakeElement:
        def __init__(self, html):
            self._html = html

        def inner_text(self, timeout=None):
            return "Test Deal Title"

        def count(self):
            return 1

        def screenshot(self, path, timeout=None):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()

    class FakeLocator:
        def __init__(self, elem):
            self.first = elem

    class FakeAccessibility:
        def snapshot(self, interesting_only=True):
            return {"name": "Buy now $19.99", "children": []}

    class FakeMouse:
        def move(self, *a, **k):
            return None

        def wheel(self, *a, **k):
            return None

    class FakePage:
        def __init__(self, html):
            self._html = html
            self.viewport_size = {"width": 1280, "height": 720}
            self.mouse = FakeMouse()
            self.accessibility = FakeAccessibility()

        def goto(self, url, wait_until=None, timeout=None):
            return None

        def content(self):
            return self._html

        def locator(self, sel):
            return FakeLocator(FakeElement(self._html))

        def screenshot(self, path, full_page=False):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()

        def close(self):
            return None

    class FakeContext:
        def __init__(self, html):
            self._html = html

        def route(self, pattern, handler):
            return None

        def new_page(self):
            return FakePage(self._html)

        def close(self):
            return None

    class FakeBrowser:
        def __init__(self, html):
            self._html = html

        def new_context(self, **kwargs):
            return FakeContext(self._html)

        def close(self):
            return None

    class FakeChromium:
        def __init__(self, html):
            self._html = html

        def launch(self, **kwargs):
            return FakeBrowser(self._html)

    class FakePlaywrightCM:
        def __init__(self, html):
            self._html = html

        def __enter__(self):
            class P:
                pass

            p = P()
            p.chromium = FakeChromium(self._html)
            return p

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright_factory():
        return FakePlaywrightCM(html)

    mod.sync_playwright = sync_playwright_factory
    return mod


def test_enrich_candidates_with_mocked_browser(tmp_path, monkeypatch):
    html = """
    <html>
      <head><title>Fake Deal</title></head>
      <body>
        <h1>Test Deal Title</h1>
        <main>
          <p>Special offer today only</p>
        </main>
        <span itemprop="price">$19.99</span>
      </body>
    </html>
    """

    # Inject fake playwright.sync_api module
    mod = make_fake_playwright_module(html)
    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.sync_api"] = mod

    # Use a temporary screenshots dir
    monkeypatch.setattr(scraper, "SCREENSHOTS_DIR", tmp_path / "shots")

    sites = [{"link": "https://fake.example/deal", "title": "Fake", "snippet": "Today only"}]

    enriched = scraper.enrich_candidates(sites)

    assert len(enriched) == 1
    e = enriched[0]
    assert e.get("scrape_method") == "playwright"
    assert "Test Deal Title" in e.get("deal_title", "")
    assert "$19.99" in e.get("deal_price", "") or e.get("deal_price") == "$19.99"
    assert e.get("screenshot_path")

    # Cleanup
    if "playwright.sync_api" in sys.modules:
        del sys.modules["playwright.sync_api"]
    if "playwright" in sys.modules:
        del sys.modules["playwright"]
