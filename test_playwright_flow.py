import sys
import types
import os
import shutil

import scraper


def test_extract_from_playwright_page_with_fake_page(tmp_path):
    # Ensure a dummy playwright.sync_api module exists for the import inside the function
    mod = types.ModuleType("playwright.sync_api")
    mod.TimeoutError = Exception
    sys.modules["playwright.sync_api"] = mod

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

    class FakeElement:
        def __init__(self, html):
            self._html = html

        def inner_text(self, timeout=None):
            return "Test Deal Title"

        def count(self):
            return 1

        def screenshot(self, path, timeout=None):
            # create an empty file to simulate screenshot
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb"):
                pass

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
            with open(path, "wb"):
                pass

    page = FakePage(html)
    # Call the extractor directly
    parsed = scraper._extract_from_playwright_page(page, "https://fake.example/deal")

    assert parsed["scrape_method"] == "playwright"
    assert "Test Deal Title" in parsed["deal_title"]
    assert "$19.99" in parsed["deal_price"] or parsed["deal_price"] == "$19.99"
    assert parsed["screenshot_path"]
    # Cleanup created screenshot
    try:
        if os.path.exists(parsed["screenshot_path"]):
            os.remove(parsed["screenshot_path"])
    except Exception:
        pass

    # Cleanup injected module
    del sys.modules["playwright.sync_api"]
