"""Shared utilities for web fetching and scraping."""

from __future__ import annotations


def needs_javascript(html: str) -> bool:
    """
    Detect if a page needs JavaScript to render properly.
    Checks for common SPA framework markers and script-heavy pages.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return False

    soup = BeautifulSoup(html, "html.parser")

    # Check noscript tags for JS warnings
    has_noscript_warning = False
    for noscript in soup.find_all("noscript"):
        text = noscript.get_text().lower()
        if "enable javascript" in text or "javascript enabled" in text:
            has_noscript_warning = True
            break

    # Check for Next.js or Nuxt.js mount points
    has_nextjs = bool(soup.find("div", id="__next"))
    has_nuxtjs = bool(soup.find("div", id="__nuxt"))

    # Check for empty Vue.js app div
    app_div = soup.find("div", id="app")
    has_empty_vue_app = app_div and not app_div.get_text(strip=True)

    # Check for Angular app-root
    has_angular_root = bool(soup.find("app-root"))

    # Check for framework-specific paths
    has_framework_paths = "/_next/static/" in html or "/_nuxt/" in html

    # Check script-to-div ratio
    script_count = html.count("<script")
    div_count = html.count("<div")
    high_script_ratio = script_count >= div_count if div_count > 0 else False

    return (
        has_noscript_warning
        or has_nextjs
        or has_nuxtjs
        or has_empty_vue_app
        or has_angular_root
        or has_framework_paths
        or high_script_ratio
    )


_DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def fetch_html_with_playwright(
    url: str,
    timeout: int = 30,
    user_agent: str = _DEFAULT_USER_AGENT,
    headless: bool = True,
) -> str:
    """
    Fetch HTML using Playwright for JavaScript-rendered pages.
    Returns the rendered HTML content.

    Uses system Chrome (channel="chrome") with basic anti-detection
    measures.  Falls back to the bundled Chromium if Chrome isn't
    installed.

    Args:
        headless: When False, opens a visible browser window.  Useful for
            sites behind Cloudflare that block headless browsers.

    Raises on failure — caller should handle exceptions.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                channel="chrome",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            browser = p.chromium.launch(headless=headless)

        context = browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            java_script_enabled=True,
        )
        page = context.new_page()

        timeout_ms = timeout * 1000
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except PlaywrightTimeout:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        page.wait_for_timeout(2000)

        html = page.content()
        browser.close()
        return html
