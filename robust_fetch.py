#!/usr/bin/env uv run
# /// script
# dependencies = [
#     "requests",
#     "curl_cffi",
#     "markitdown",
#     "beautifulsoup4",
#     "playwright"
# ]
# ///

"""
Robust URL fetching with multiple fallback methods.

This script attempts to fetch a URL using multiple methods in order:
1. curl_cffi (browser impersonation to bypass anti-bot measures)
   - Automatically detects if the page needs JavaScript to render
   - Uses Playwright to render JS-heavy pages when needed
2. playwright (force browser rendering for JS-heavy pages)
3. markitdown (direct URL fetching and conversion)
4. jina.ai reader (web content extraction service)
5. Firecrawl API (advanced scraping service with PDF support)

Usage:
    rf <url> [--timeout SECONDS] [--scraper SCRAPER]

Options:
    --timeout SECONDS       Timeout in seconds for each method (default: 30)
    -s, --scraper SCRAPER   Force a specific scraper (curl, playwright/pw, markitdown/mid, jina, firecrawl/fc)

Returns:
    The fetched content on stdout
    Exit code 0 on success, 1 on failure
"""

import argparse
import io
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from fetch_utils import fetch_html_with_playwright, needs_javascript

# Scraper aliases mapping to fetch functions
SCRAPER_ALIASES: Dict[str, str] = {
    "curl": "curl_cffi",
    "playwright": "playwright",
    "pw": "playwright",
    "markitdown": "markitdown",
    "mid": "markitdown",
    "jina": "jina_reader",
    "firecrawl": "firecrawl",
    "fc": "firecrawl",
}


def normalize_url(url: str) -> str:
    """
    Normalize a URL to ensure it has a proper scheme (https://).

    Examples:
        example.com -> https://example.com
        www.example.com -> https://www.example.com
        http://example.com -> http://example.com (unchanged)
        https://example.com -> https://example.com (unchanged)
    """
    url = url.strip()

    # If the URL already has a scheme, return as-is
    if url.startswith(("http://", "https://")):
        return url

    # Otherwise, add https:// prefix
    return f"https://{url}"


def build_jina_reader_url(url: str) -> str:
    """Build r.jina.ai reader URL for a target page."""
    parsed = urllib.parse.urlparse(url)
    # Use http scheme within the reader path; it will follow redirects as needed
    target = f"http://{parsed.netloc}{parsed.path}"
    if parsed.query:
        target += f"?{parsed.query}"
    return f"https://r.jina.ai/{target}"


def is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF file."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    return path.endswith(".pdf") or ".pdf?" in path or ".pdf#" in path


def fetch_with_playwright(url: str, timeout: int) -> str:
    """
    Fetch URL using Playwright and convert to markdown.
    This is a standalone method for forced Playwright usage.
    """
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise ImportError(
            "markitdown not installed. Are you sure you ran this script with `uv run`?"
        )

    html_content = fetch_html_with_playwright(url, timeout)

    # Convert HTML to markdown using MarkItDown
    stream = io.BytesIO(html_content.encode('utf-8'))
    md = MarkItDown()
    result = md.convert_stream(stream, file_extension=".html")

    return result.text_content


def try_markdown_url(url: str, timeout: int) -> Optional[str]:
    """
    Try fetching URL with .md suffix appended.
    Many documentation sites serve raw markdown at URL.md paths.

    Returns the markdown content if successful, None otherwise.
    """
    try:
        import requests
    except ImportError:
        return None

    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip('/')

    # Skip if already ends with .md
    if path.endswith('.md'):
        return None

    # Skip if no meaningful path (just domain, e.g., example.com or example.com/)
    if not path or path == '/':
        return None

    # Build URL with .md suffix
    md_url = url.rstrip('/') + '.md'

    try:
        response = requests.get(
            md_url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/markdown, text/plain, */*",
            },
            allow_redirects=True,
        )

        # Check for success
        if response.status_code != 200:
            return None

        content_type = response.headers.get('Content-Type', '').lower()
        content = response.text

        # Validate it looks like markdown (not HTML error page)
        if '<html' in content.lower()[:500] or '<!doctype' in content.lower()[:500]:
            return None

        # Check content-type suggests text/markdown or text/plain
        if not any(ct in content_type for ct in ['text/markdown', 'text/plain', 'text/x-markdown']):
            # Content-type doesn't match, but content might still be valid markdown
            # Check if it has markdown-like content (headers, links, etc.)
            if not any(marker in content[:1000] for marker in ['# ', '## ', '](', '```', '- ', '* ']):
                return None

        print(f"[robust_fetch] Found raw markdown at: {md_url}", file=sys.stderr)
        return content

    except Exception:
        return None


def fetch_with_curl_cffi(url: str, timeout: int) -> str:
    """
    Attempt to fetch URL using curl_cffi with browser impersonation.
    First tries to fetch raw markdown at URL.md if available.
    If the page needs JavaScript, automatically uses Playwright to render it.
    """
    # First, try fetching raw markdown at URL.md
    md_content = try_markdown_url(url, timeout)
    if md_content:
        return md_content

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        raise ImportError(
            "curl_cffi not installed. Are you sure you ran this script with `uv run`?"
        )

    try:
        from markitdown import MarkItDown
    except ImportError:
        raise ImportError(
            "Can't convert HTML response to markdown: markitdown not installed. Are you sure you ran this script with `uv run`?"
        )

    response = curl_requests.get(
        url,
        impersonate="chrome131",
        timeout=timeout,
        allow_redirects=True,
        headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        },
    )
    response.raise_for_status()

    html_content = response.text

    # Check if the page needs JavaScript to render properly
    if needs_javascript(html_content):
        print(f"[robust_fetch] Detected JS-rendered page, using Playwright for: {url}", file=sys.stderr)
        # Refetch with Playwright to get rendered content
        html_content = fetch_html_with_playwright(url, timeout)

    # Convert HTML to markdown using MarkItDown
    stream = io.BytesIO(html_content.encode('utf-8'))
    md = MarkItDown()
    result = md.convert_stream(stream, file_extension=".html")

    return result.text_content


def fetch_with_markitdown(url: str, timeout: int) -> str:
    """Attempt to fetch and convert URL using MarkItDown."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise ImportError(
            "markitdown not installed. Are you sure you ran this script with `uv run`?"
        )

    # MarkItDown can fetch URLs directly
    result = MarkItDown().convert(url, enable_builtins=True, enable_plugins=True)
    if hasattr(result, 'text_content'):
        return result.text_content
    return str(result)


def fetch_with_jina_reader(url: str, timeout: int) -> str:
    """Attempt to fetch URL using jina.ai reader service."""
    try:
        import requests
    except ImportError:
        raise ImportError("requests not installed. Are you sure you ran this script with `uv run`?")

    reader_url = build_jina_reader_url(url)
    response = requests.get(
        reader_url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RobustFetch/1.0)"},
    )
    response.raise_for_status()

    # Check if jina returned an error page
    import re

    if re.search(r"error \d+", response.text, flags=re.IGNORECASE):
        from textwrap import shorten
        raise Exception(f"Jina reader returned an error page: {shorten(response.text.strip(), 100)}")

    return response.text


def fetch_with_firecrawl(url: str, timeout: int) -> str:
    """
    Attempt to fetch URL using Firecrawl API.

    Args:
        url: The URL to fetch
        timeout: Timeout in seconds (converted to milliseconds for Firecrawl)

    Returns:
        The fetched markdown content

    Raises:
        ImportError: If requests is not installed
        FileNotFoundError: If API key file is not found
        Exception: If API request fails
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests not installed. Are you sure you ran this script with `uv run`?")

    # Read API key
    api_key = None
    api_key_path = Path.home() / ".firecrawl-api-key-hearai"
    if api_key_path.is_file():
        api_key = api_key_path.read_text().strip()
    else:
        import os
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            raise ValueError("~/.firecrawl-api-key-hearai file not found and FIRECRAWL_API_KEY environment variable is not set")

    # Build request payload
    payload = {
        "url": url,
        "onlyMainContent": True,
        "waitFor": 4000,
        "location": {"country": "US", "languages": ["en-US"]},
        "timeout": timeout * 1000,  # Convert seconds to milliseconds
        "formats": ["markdown"],
    }

    # Add PDF parser if URL points to a PDF
    if is_pdf_url(url):
        payload["parsers"] = ["pdf"]

    # Make API request
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    api_url = "https://api.firecrawl.dev/v1/scrape"
    response = requests.post(api_url, json=payload, headers=headers, timeout=timeout)

    # Handle response
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_msg = error_data.get("error", f"HTTP {response.status_code}")
        except Exception:
            error_msg = f"HTTP {response.status_code}"
        raise Exception(f"Firecrawl API error: {error_msg}")

    # Parse successful response
    data = response.json()

    if not data.get("success"):
        error_msg = data.get("error", "Unknown error")
        raise Exception(f"Firecrawl scrape failed: {error_msg}")

    # Extract markdown content
    scrape_data = data.get("data", {})
    metadata = data.get("metadata", {})
    if "not found" in metadata.get("title", "").lower():
        raise Exception(f"Page not found: {url}")

    if metadata.get("statusCode", 200) != 200:
        raise Exception(f"HTTP {metadata.get('statusCode')}: {url}")

    markdown = scrape_data.get("markdown")

    if not markdown:
        # Check for errors in metadata
        metadata = scrape_data.get("metadata", {})
        if "error" in metadata:
            raise Exception(f"Firecrawl metadata error: {metadata['error']}")
        raise Exception("Firecrawl did not return markdown content")

    return markdown


def robust_fetch(url: str, timeout: int = 30, scraper: Optional[str] = None) -> str:
    """
    Fetch URL content using multiple fallback methods.

    Args:
        url: The URL to fetch
        timeout: Timeout in seconds for each method
        scraper: Optional scraper to force (e.g., 'curl', 'playwright', 'jina')

    Returns:
        The fetched content as a string

    Raises:
        Exception: If all methods fail or specified scraper fails
    """
    # Map of method names to fetch functions
    method_map: Dict[str, Callable[[str, int], str]] = {
        "curl_cffi": fetch_with_curl_cffi,
        "playwright": fetch_with_playwright,
        "markitdown": fetch_with_markitdown,
        "jina_reader": fetch_with_jina_reader,
        "firecrawl": fetch_with_firecrawl,
    }

    # If a specific scraper is requested, use only that one
    if scraper:
        # Resolve alias to method name
        method_name = SCRAPER_ALIASES.get(scraper)
        if not method_name:
            available = ", ".join(sorted(set(SCRAPER_ALIASES.keys())))
            raise ValueError(f"Unknown scraper '{scraper}'. Available: {available}")

        fetch_func = method_map.get(method_name)
        if not fetch_func:
            raise ValueError(f"Internal error: method '{method_name}' not found")

        print(f"[robust_fetch] Using forced scraper: {method_name}", file=sys.stderr)
        try:
            return fetch_func(url, timeout)
        except Exception as e:
            raise Exception(f"Forced scraper '{scraper}' ({method_name}) failed: {e}")

    # Otherwise, try all methods in order
    methods = [
        ("curl_cffi", fetch_with_curl_cffi),
        ("playwright", fetch_with_playwright),
        ("markitdown", fetch_with_markitdown),
        ("jina_reader", fetch_with_jina_reader),
        ("firecrawl", fetch_with_firecrawl),
    ]

    errors = []

    for method_name, fetch_func in methods:
        try:
            content = fetch_func(url, timeout)
            # Success - print which method worked if there were previous failures
            if errors:
                print(
                    f"[robust_fetch] {method_name} succeeded after {len(errors)} failed attempt(s)",
                    file=sys.stderr,
                )
            return content
        except Exception as e:
            error_msg = f"{method_name}: {e}"
            errors.append(error_msg)
            print(f"[robust_fetch] {error_msg}", file=sys.stderr)
            continue

    # All methods failed
    raise Exception(f"All fetch methods failed for {url}. Errors: {'; '.join(errors)}")


def main():
    parser = argparse.ArgumentParser(
        description="Robustly fetch URL content with multiple fallback methods"
    )
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for each method (default: 30)",
    )
    parser.add_argument(
        "-s",
        "--scraper",
        type=str,
        default=None,
        help="Force a specific scraper (curl, playwright/pw, markitdown/mid, jina, firecrawl/fc)",
    )

    args = parser.parse_args()

    # Normalize URL to ensure it has a proper scheme
    url = normalize_url(args.url)

    try:
        content = robust_fetch(url, args.timeout, args.scraper)
        # Add frontmatter with the URL and created date
        created_date = datetime.now().strftime("%Y-%m-%d")
        output = f"---\nurl: {url}\ncreated: {created_date}\n---\n\n{content}"
        print(output)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
