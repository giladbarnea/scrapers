import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fetch_utils


def test_empty_list_placeholders_signal_javascript() -> None:
    # JS-populated nav menus ship as empty <ul> placeholders in static HTML
    # (e.g., zunit.xyz/docs), alongside a few static links that defeat the
    # "no links found" fallback.
    html = """
    <html><body>
      <a href="/docs">Docs</a>
      <nav>
        <h4>Getting Started</h4>
        <ul class="nav--getting-started"></ul>
        <h4>Usage</h4>
        <ul class="nav--usage"></ul>
      </nav>
    </body></html>
    """
    assert fetch_utils.needs_javascript(html)


def test_single_empty_list_is_not_enough() -> None:
    html = """
    <html><body>
      <ul class="cart-items"></ul>
      <ul><li><a href="/about">About</a></li></ul>
    </body></html>
    """
    assert not fetch_utils.needs_javascript(html)


def test_populated_lists_do_not_signal_javascript() -> None:
    html = """
    <html><body>
      <ul><li><a href="/a">A</a></li></ul>
      <ul><li><a href="/b">B</a></li></ul>
    </body></html>
    """
    assert not fetch_utils.needs_javascript(html)
