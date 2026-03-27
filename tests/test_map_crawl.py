import json
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import map_crawl


def _html_with_links(*hrefs: str) -> str:
    anchors = "".join(f'<a href="{href}">link</a>' for href in hrefs)
    return f"<html><body>{anchors}</body></html>"


def test_crawl_fetches_each_frontier_in_parallel_and_preserves_distance(tmp_path, monkeypatch) -> None:
    output_path = tmp_path / "crawl.json"

    a_started = threading.Event()
    b_started = threading.Event()
    b_finished = threading.Event()

    def fake_fetch_html(urls_to_try, _client):
        url = urls_to_try[0]
        canon = map_crawl.canonical_key(url)

        if canon == "example.com/docs":
            return url, _html_with_links("/docs/a", "/docs/b")

        if canon == "example.com/docs/a":
            a_started.set()
            assert b_started.wait(0.5), (
                "Expected /docs/a and /docs/b to be fetched in parallel within the same frontier."
            )
            assert not b_finished.is_set(), (
                "Expected /docs/b to still be in flight when /docs/a finished."
            )
            return url, _html_with_links("/docs/x")

        if canon == "example.com/docs/b":
            b_started.set()
            assert a_started.wait(0.5), (
                "Expected /docs/a and /docs/b to be fetched in parallel within the same frontier."
            )
            time.sleep(0.05)
            b_finished.set()
            return url, _html_with_links()

        if canon == "example.com/docs/x":
            assert b_finished.is_set(), (
                "Expected the next frontier to wait until the current frontier finished."
            )
            return url, _html_with_links()

        raise AssertionError(f"Unexpected fetch for canonical URL {canon!r}")

    def fail_playwright(_url: str) -> str:
        raise AssertionError("Did not expect Playwright fallback in this test.")

    monkeypatch.setattr(map_crawl, "fetch_html", fake_fetch_html)
    monkeypatch.setattr(map_crawl, "needs_javascript", lambda _html: False)
    monkeypatch.setattr(map_crawl, "fetch_html_with_playwright", fail_playwright)

    map_crawl.crawl(
        "https://example.com/docs",
        json_path=str(output_path),
        discover=False,
        workers=2,
    )

    data = json.loads(output_path.read_text())
    assert data == {
        "urls": {
            "0": ["example.com/docs/a", "example.com/docs/b"],
            "1": ["example.com/docs/x"],
        }
    }, f"Expected BFS distance tiers in the JSON output. Got: {data!r}"
