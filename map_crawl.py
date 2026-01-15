#!/usr/bin/env uv run
# /// script
# dependencies = [
#     "httpx",
#     "beautifulsoup4",
#     "lxml",
#     "playwright",
# ]
# ///

"""
1) Metadata you can fetch (and how reliable)

• HTTP response headers (via httpx):
  • Last-Modified, ETag, Date, Content-Type, Cache-Control. Useful but Last-Modified often reflects deploy/cache, not creation.
• HTML head/body (via BeautifulSoup):
  • title, link rel=canonical, meta name=description, meta name=author.
  • Open Graph: og:title, og:description, og:url, article:published_time, article:modified_time, article:author, article:tag.
  • Twitter Card: twitter:title, twitter:description, twitter:labelX/dataX.
  • JSON-LD (schema.org Article/BlogPosting): datePublished, dateModified, headline, author, breadcrumb, keywords.
  • Microformats/microdata: h-entry (dt-published), itemprop=datePublished/dateModified.
  • time[datetime] elements and visible byline dates.
  • link rel=prev/next, link rel=alternate (feeds), link rel=author.
• Feeds and sitemaps (extra GETs to first-party URLs):
  • RSS/Atom (linked via rel=alternate): pubDate/updated; match item by link to infer publish time.
  • robots.txt → sitemap URLs → sitemap.xml/urlset (lastmod) or sitemapindex.
• Practical date strategy (fallback order):
  1. JSON-LD datePublished
  2. OG article:published_time
  3. time[datetime] or itemprop=datePublished
  4. Feed pubDate/updated (matched by URL)
  5. Sitemap lastmod (if item present)
  6. Last-Modified header
  7. Date in URL path (e.g., /2024/11/…) as low-confidence
• Consistency: title/canonical/OG/Twitter are near-universal on modern blogs; a true creation date is “often” available (JSON-LD/OG/feed) but not guaranteed. Expect high coverage on CMS-backed blogs; degrade gracefully with confidence
  flags.


2) Non-ML “cause-and-effect” reading path using links + chronology

• Build a content-only link graph:
  • Extract only in-article links (limit to within article/main tags; exclude header/footer/nav/related-posts).
  • Keep edges u→v when u links to v.
• Timestamp every node:
  • Use the date strategy above, store best date and a confidence score.
• Enforce chronology to break cycles:
  • Keep edges where published_at(u) ≤ published_at(v). If one date unknown, keep edge but mark low-confidence; optionally drop unknowns.
  • Condense same-timestamp ties by ordering via URL slug/time-of-day if available.
• Score edges (strength of “depends on”):
  • +1.0 base if u older than v.
  • +0.5 if link appears in first N paragraphs.
  • +0.5 if anchor text matches {“part”, “series”, “intro”, “background”, “deep dive”, “continued”, “previous”}.
  • +0.5 if same tag/category; +0.25 if same section path prefix.
  • +1.0 if rel=prev/next series hints (or explicit series nav).
  • −1.0 if in nav/footer/related-post blocks.
• Compute the “best” reading path to target T:
  • Create a DAG by removing time-violating edges, then run longest-path-by-weight DP on the reversed graph ending at T. That yields a single spine path: earliest plausible starting post → … → T.
  • For branching prerequisites, produce a minimal set of paths:
    • Greedy Steiner-like cover: starting at T, repeatedly add the highest-weight predecessor chain not already covered, up to K paths or until marginal gain < threshold.
    • Cap breadth (e.g., top 1–2 predecessors per hop) to keep lists readable.
• Optional clustering (non-ML):
  • Community detection on the undirected version (e.g., label propagation) or simple connected components within a rolling time window (e.g., 90 days) to group posts into themes/series.
  • Use cluster membership to bias edge scores (favor predecessors in the same cluster).


Feasibility summary

• Creation date: often retrievable (JSON-LD/OG/feed) on modern blogs; not universal. Provide confidence levels and fallbacks.
• Cause-effect path: very feasible with link graph + chronology + light heuristics. Longest-path in a time-respecting graph gives a clear primary reading spine; add 0–2 side branches by edge strength for coverage without noise.

If you want, I can extend the script to:
• Extract publish/modified dates with confidence,
• Restrict edges to in-article links,
• Compute and print the best reading path(s) to a target post.

---

# /usr/local/bin/mc
```sh
#!/usr/bin/env zsh

main(){
  local arg
  local seen_json_arg=false
  local -a args
  for arg in "$@"; do
    if [[ "$arg" = -j || "$arg" = --json ]]; then
      seen_json_arg=true
    fi
    args+=("$1")
  done
  local json_destination
  if [[ "$seen_json_arg" = false ]]; then
    json_destination="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 4)"
    echo "[WARNING] [mc] -j,--json arg was not provided. Writing results to /tmp/$json_destination.json. Read with ‘jq .urls /tmp/$json_destination.json -r’" 1>&2
    args+=(-j "$json_destination")
  fi
  $HOME/dev/scrapers/map_crawl.py "${args[@]}"
}
main "$@"
```
"""

import argparse
import contextlib
import gzip
import json
import pathlib
import posixpath
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# File extensions that clearly indicate non-page assets we should not follow/store
ASSET_EXTENSIONS: Set[str] = {
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".rar",
    ".7z",
    ".mp3",
    ".wav",
    ".ogg",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


def strip_www_and_port(netloc: str) -> str:
    host = netloc.lower()
    # drop userinfo if any (rare in links)
    if "@" in host:
        host = host.split("@", 1)[1]
    # drop port
    if ":" in host:
        host = host.split(":", 1)[0]
    # drop leading www.
    if host.startswith("www."):
        host = host[4:]
    # drop trailing dot
    if host.endswith("."):
        host = host[:-1]
    return host


def normalize_path(path: str, strip_trailing_slash: bool = True) -> str:
    """
    Normalize a URL path:
    - Defaults to "/" if empty
    - Collapses duplicate slashes
    - Normalizes dot segments (../../etc)
    - Ensures leading slash
    - Optionally removes trailing slash (unless root)
    """
    if not path:
        path = "/"

    # Collapse duplicate slashes
    path = re.sub(r"/+", "/", path)

    # Normalize dot segments
    try:
        path = posixpath.normpath(path)
    except Exception:
        pass  # Keep as-is if normpath fails

    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path

    # Remove trailing slash (unless root)
    if strip_trailing_slash and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return path


def canonical_key(url: str) -> str:
    """
    Canonical key that is insensitive to scheme, www, port, and trailing slash.
    Example keys:
      - https://www.example.com/   -> example.com
      - http://example.com/about/  -> example.com/about
    Query params and fragments are discarded.
    """
    p = urlparse(url)
    if not p.netloc:
        return ""
    host = strip_www_and_port(p.netloc)

    path = normalize_path(p.path or "/")

    if path == "/":
        return host
    return f"{host}{path}"


def same_domain(url: str, allowed_domain: str) -> bool:
    p = urlparse(url)
    if not p.netloc:
        return False
    return strip_www_and_port(p.netloc) == allowed_domain


def within_path_prefix(url: str, allowed_domain: str, path_prefix: str) -> bool:
    """
    Check if url is within the allowed domain and path prefix.
    If path_prefix is empty, any path on the domain is allowed.
    """
    if not same_domain(url, allowed_domain):
        return False
    if not path_prefix:
        return True

    p = urlparse(url)
    url_path = normalize_path(p.path or "/", strip_trailing_slash=False)

    return url_path.startswith(path_prefix) or url_path == path_prefix.rstrip("/")


def parse_filter_spec(spec: str, seed_domain: str) -> Tuple[str, str]:
    """
    Parse a filter specification into (domain, path_prefix).

    Supports flexible input formats:
    - Full URLs: https://docs.cloud.google.com/pubsub/docs -> (docs.cloud.google.com, /pubsub/docs)
    - URLs with www: www.example.com/path -> (example.com, /path)
    - Domain + path: example.com/foo/bar -> (example.com, /foo/bar)
    - Bare paths: pubsub/docs or /pubsub/docs -> (seed_domain, /pubsub/docs)

    Args:
        spec: Filter specification in any of the above formats
        seed_domain: Domain from seed URL (used as fallback for bare paths)

    Returns:
        (domain, path_prefix) tuple. path_prefix has leading slash, no trailing slash (unless root).
        Empty path_prefix means no path filtering (domain-only).

    Examples:
        parse_filter_spec("https://example.com/foo/bar", "seed.com")
            -> ("example.com", "/foo/bar")
        parse_filter_spec("www.example.com/foo/bar", "seed.com")
            -> ("example.com", "/foo/bar")
        parse_filter_spec("example.com/foo", "seed.com")
            -> ("example.com", "/foo")
        parse_filter_spec("foo/bar", "seed.com")
            -> ("seed.com", "/foo/bar")
        parse_filter_spec("/foo/bar", "seed.com")
            -> ("seed.com", "/foo/bar")
        parse_filter_spec("example.com", "seed.com")
            -> ("example.com", "")
    """
    if not spec:
        return seed_domain, ""

    spec = spec.strip()

    # Remove scheme if present (idempotent - can be called multiple times)
    spec = re.sub(r'^https?://', '', spec, flags=re.IGNORECASE)
    # Remove www. prefix (idempotent)
    spec = re.sub(r'^www\.', '', spec, flags=re.IGNORECASE)

    # Find first slash to separate domain from path
    first_slash = spec.find('/')

    if first_slash == -1:
        # No slash - could be domain-only or bare path segment
        if '.' in spec:
            # It's a domain without path (e.g., "example.com")
            return strip_www_and_port(spec), ""
        else:
            # It's a bare path segment (e.g., "pubsub")
            path = normalize_path('/' + spec, strip_trailing_slash=True)
            return seed_domain, path
    else:
        # Has slash - check if first component is a domain
        first_component = spec[:first_slash]
        if '.' in first_component:
            # It's domain + path (e.g., "example.com/foo/bar")
            domain = strip_www_and_port(first_component)
            path_part = spec[first_slash:]
            path = normalize_path(path_part, strip_trailing_slash=True)
            return domain, path
        else:
            # It's just a path without domain (e.g., "foo/bar")
            path = normalize_path('/' + spec, strip_trailing_slash=True)
            return seed_domain, path


def resolve_and_strip(base_url: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    # ignore non-http(S) schemes and other non-page targets
    if re.match(r"^(javascript:|mailto:|tel:|data:|sms:|ftp:)", href, re.IGNORECASE):
        return ""
    abs_url = urljoin(base_url, href)
    parts = list(urlparse(abs_url))
    # strip query and fragment
    parts[4] = ""  # query
    parts[5] = ""  # fragment
    return urlunparse(parts)


def extract_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: List[str] = []

    # Only follow anchor tags; skip <link> which commonly targets CSS/images/etc
    for tag in soup.find_all(["a"]):
        href = tag.get("href")
        if href:
            resolved = resolve_and_strip(base_url, href)
            if resolved:
                # Skip obvious asset URLs by extension
                parsed = urlparse(resolved)
                _, ext = posixpath.splitext(parsed.path)
                if ext.lower() in ASSET_EXTENSIONS:
                    continue
                candidates.append(resolved)

    # de-duplicate while preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def load_store(path: str) -> Dict[str, List[str]]:
    """
    Load adjacency mapping from a file that may be in new or old format.

    New format: { "urls": [...], <canon>: [<canon> ...], ... }
    Old format: { <canon>: [<canon> ...], ... }
    """
    if not pathlib.Path(path).exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "urls" in data:
                data = {k: v for k, v in data.items() if k != "urls"}
            # ensure correct types
            return {str(k): [str(v) for v in (vs or [])] for k, vs in data.items()}
    except Exception:
        # backup corrupted file and start fresh
        with contextlib.suppress(Exception):
            pathlib.Path(path).replace(path + ".bak")
        return {}


def write_store(mapping: Dict[str, List[str]], path: str) -> None:
    """
    Write store in DRY per-domain format with a top-level union 'urls' list.

    The output JSON shape is:
    { "urls": [ ... ], <canon>: [<canon> ...], ... }
    """
    # Build union of all URLs across keys and values
    all_nodes: Set[str] = set(mapping.keys())
    for vs in mapping.values():
        all_nodes.update(vs)

    data_out: Dict[str, object] = {"urls": sorted(all_nodes)}
    # Preserve insertion order of keys (Python 3.7+), do not sort keys so 'urls' stays on top
    data_out.update(mapping)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data_out, f, indent=2, sort_keys=False, ensure_ascii=False)
    pathlib.Path(tmp).replace(path)


def is_page_like_canon(canon: str) -> bool:
    _host, _, rest = canon.partition("/")
    path = "/" + rest if rest else "/"
    # Allow host roots and path-like pages without asset extensions
    if path == "/":
        return True
    _, ext = posixpath.splitext(path)
    return ext.lower() in {"", ".html", ".htm"}


def clean_mapping_assets(mapping: Dict[str, List[str]]) -> Dict[str, List[str]]:
    cleaned: Dict[str, List[str]] = {}
    for k, vs in mapping.items():
        if not is_page_like_canon(k):
            continue
        filtered_vs = [v for v in vs if is_page_like_canon(v)]
        # de-duplicate and sort for stability
        uniq: List[str] = []
        seen: Set[str] = set()
        for item in filtered_vs:
            if item not in seen:
                seen.add(item)
                uniq.append(item)
        cleaned[k] = uniq
    return cleaned


def pick_fetch_urls(
    canon: str, sample_full_url: Optional[str], seed_scheme: Optional[str]
) -> List[str]:
    host, _, rest = canon.partition("/")
    path = "/" + rest if rest else "/"
    urls: List[str] = []
    if sample_full_url:
        urls.append(sample_full_url)
    if seed_scheme:
        urls.append(f"{seed_scheme}://{host}{path}")
    urls.append(f"https://{host}{path}")
    urls.append(f"http://{host}{path}")
    # de-dupe preserve order
    out: List[str] = []
    seen: Set[str] = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_html(
    urls_to_try: List[str], client: httpx.Client
) -> Optional[Tuple[str, str]]:
    for u in urls_to_try:
        try:
            r = client.get(u, timeout=15)
            if r.status_code >= 400:
                continue
            ctype = r.headers.get("content-type", "").lower()
            if "html" not in ctype:
                continue
            return u, r.text
        except Exception:
            continue
    return None


def fetch_html_playwright(url: str) -> Optional[Tuple[str, str]]:
    """
    Fetch HTML using Playwright for JavaScript-rendered pages.
    Returns (url, html) or None if failed.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="stupid-simple-crawler/0.1 (+https://example.invalid)"
            )
            page = context.new_page()

            # Navigate and wait for network to be idle
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                # Try with just domcontentloaded if networkidle times out
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait a bit more for any dynamic content
            page.wait_for_timeout(1000)

            html = page.content()
            browser.close()
            return url, html
    except Exception as e:
        print(f"Playwright fetch failed for {url}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# URL Discovery (robots.txt, sitemaps, llms.txt, feeds)
# ---------------------------------------------------------------------------

FEED_PATHS = ["/feed/", "/feed.xml", "/rss/", "/rss.xml", "/atom.xml"]


def _strip_doc_extension(canon: str) -> str:
    """Strip .md/.html/.htm to get base for deduplication."""
    for ext in (".md", ".html", ".htm"):
        if canon.endswith(ext):
            return canon[: -len(ext)]
    return canon


def merge_urls_with_md_preference(urls: Iterable[str]) -> Set[str]:
    """Merge URLs, preferring .md versions over others."""
    by_base: Dict[str, str] = {}
    for url in urls:
        canon = canonical_key(url)
        if not canon:
            continue
        base = _strip_doc_extension(canon)
        existing = by_base.get(base)
        if existing is None:
            by_base[base] = canon
        elif canon.endswith(".md") and not existing.endswith(".md"):
            by_base[base] = canon  # .md wins
    return set(by_base.values())


def fetch_text(
    url: str, client: httpx.Client, accept_xml: bool = False
) -> Optional[str]:
    """Fetch URL, handle gzip, return text or None."""
    try:
        headers = {}
        if accept_xml:
            headers["Accept"] = "application/xml, text/xml, */*"
        r = client.get(url, timeout=15, headers=headers)
        if r.status_code >= 400:
            return None
        # Handle gzip
        if url.endswith(".gz") or r.headers.get("content-encoding") == "gzip":
            return gzip.decompress(r.content).decode("utf-8")
        return r.text
    except Exception:
        return None


def parse_robots_txt(content: str, base_url: str) -> Tuple[List[str], List[str]]:
    """Extract Sitemap URLs and Disallow paths from robots.txt.

    Returns (sitemap_urls, disallow_page_urls).
    Disallow paths can reveal interesting site structure even when blocking crawlers.
    """
    sitemaps = []
    disallow_urls = []
    for line in content.splitlines():
        line = line.strip()
        lower = line.lower()
        if lower.startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                sitemaps.append(urljoin(base_url, url))
        elif lower.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            # Skip wildcards, empty, and root-only paths
            if path and "*" not in path and path != "/":
                disallow_urls.append(urljoin(base_url, path))
    return sitemaps, disallow_urls


def parse_sitemap(content: str) -> Tuple[List[str], List[str]]:
    """
    Parse sitemap XML.
    Returns (page_urls, nested_sitemap_urls).
    """
    soup = BeautifulSoup(content, "lxml-xml")
    page_urls = []
    nested_sitemaps = []

    # Check for sitemapindex
    for sitemap in soup.find_all("sitemap"):
        loc = sitemap.find("loc")
        if loc and loc.string:
            nested_sitemaps.append(loc.string.strip())

    # Check for urlset
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if loc and loc.string:
            page_urls.append(loc.string.strip())

    return page_urls, nested_sitemaps


def fetch_sitemap_recursive(
    url: str, client: httpx.Client, visited: Set[str], max_depth: int = 3
) -> List[str]:
    """Recursively fetch sitemaps, return all page URLs."""
    if url in visited or max_depth <= 0:
        return []
    visited.add(url)

    content = fetch_text(url, client, accept_xml=True)
    if not content:
        return []

    pages, nested = parse_sitemap(content)

    for nested_url in nested:
        pages.extend(fetch_sitemap_recursive(nested_url, client, visited, max_depth - 1))

    return pages


def parse_llms_txt(content: str, base_url: str) -> List[str]:
    """Extract URLs from llms.txt content (handles both absolute and relative)."""
    urls = []

    # Markdown links: [text](url) - capture any href (absolute or relative)
    md_link = re.compile(r"\[.*?\]\(([^\s)]+)\)")
    # Reference links: [text]: url
    md_ref = re.compile(r"\[.*?\]:\s*(\S+)")
    # Bare URLs (with scheme) or paths (starting with /)
    bare_url_or_path = re.compile(r"(?:^|\s)((?:https?://|/)\S+)")

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        found: List[str] = []
        # Try markdown link patterns first
        found.extend(md_link.findall(line))
        found.extend(md_ref.findall(line))

        # If no markdown links found, try bare URL/path
        if not found:
            found.extend(bare_url_or_path.findall(line))

        urls.extend(found)

    # Resolve all URLs (urljoin handles both absolute and relative)
    return [urljoin(base_url, u) for u in urls]


def parse_feed(content: str) -> Tuple[List[str], Optional[str]]:
    """Extract entry URLs and next page URL from RSS or Atom feed.

    Returns (entry_urls, next_page_url).
    """
    soup = BeautifulSoup(content, "lxml-xml")
    urls = []
    next_url = None

    # RSS 2.0
    for item in soup.find_all("item"):
        link = item.find("link")
        if link and link.string:
            urls.append(link.string.strip())
        else:
            # Fallback to guid if permalink
            guid = item.find("guid")
            if guid and guid.get("isPermaLink", "").lower() == "true":
                if guid.string:
                    urls.append(guid.string.strip())

    # Atom
    for entry in soup.find_all("entry"):
        for link in entry.find_all("link"):
            href = link.get("href")
            rel = link.get("rel", "alternate")
            if href and rel in ("alternate", None):
                urls.append(href)

    # Check for pagination (rel="next") - works for both RSS and Atom
    for link in soup.find_all("link"):
        rel = link.get("rel")
        if rel == "next":
            next_url = link.get("href")
            break

    return urls, next_url


def fetch_feed_with_pagination(
    url: str, client: httpx.Client, visited: Optional[Set[str]] = None, max_pages: int = 5
) -> List[str]:
    """Fetch feed with pagination support, return all entry URLs."""
    if visited is None:
        visited = set()
    if url in visited or max_pages <= 0:
        return []
    visited.add(url)

    content = fetch_text(url, client, accept_xml=True)
    if not content:
        return []

    entry_urls, next_url = parse_feed(content)

    if next_url:
        resolved_next = urljoin(url, next_url)
        if resolved_next not in visited:
            entry_urls.extend(
                fetch_feed_with_pagination(resolved_next, client, visited, max_pages - 1)
            )

    return entry_urls


def discover_from_feeds(base_url: str, client: httpx.Client) -> List[str]:
    """Try common feed endpoints, return URLs from first successful one (with pagination)."""
    for path in FEED_PATHS:
        url = urljoin(base_url, path)
        urls = fetch_feed_with_pagination(url, client)
        if urls:
            return urls
    return []


def parse_html_head_links(content: str, base_url: str) -> Tuple[List[str], List[str]]:
    """Extract sitemap and feed URLs from HTML <link> tags.

    Returns (sitemap_urls, feed_urls).
    """
    soup = BeautifulSoup(content, "html.parser")
    sitemap_urls = []
    feed_urls = []

    for link in soup.find_all("link"):
        rel = link.get("rel", [])
        if isinstance(rel, list):
            rel = " ".join(rel)
        rel = rel.lower()
        href = link.get("href")
        if not href:
            continue

        resolved = urljoin(base_url, href)

        if "sitemap" in rel:
            sitemap_urls.append(resolved)
        elif "alternate" in rel:
            link_type = (link.get("type") or "").lower()
            if "rss" in link_type or "atom" in link_type or "xml" in link_type:
                feed_urls.append(resolved)

    return sitemap_urls, feed_urls


def discover_urls(base_url: str, client: httpx.Client) -> Set[str]:
    """
    Discover URLs from all sources in parallel.
    Returns deduplicated set with .md preference.
    """
    all_urls: List[str] = []

    def discover_from_robots() -> List[str]:
        content = fetch_text(urljoin(base_url, "/robots.txt"), client)
        if not content:
            return []
        sitemap_urls, disallow_urls = parse_robots_txt(content, base_url)
        pages: List[str] = list(disallow_urls)  # Disallow paths as seed hints
        visited: Set[str] = set()
        for sm_url in sitemap_urls:
            pages.extend(fetch_sitemap_recursive(sm_url, client, visited))
        return pages

    def discover_from_sitemap_direct() -> List[str]:
        # Try common sitemap locations directly
        for path in ["/sitemap.xml", "/sitemap_index.xml"]:
            urls = fetch_sitemap_recursive(urljoin(base_url, path), client, set())
            if urls:
                return urls
        return []

    def discover_from_llms() -> List[str]:
        content = fetch_text(urljoin(base_url, "/llms.txt"), client)
        return parse_llms_txt(content, base_url) if content else []

    def discover_from_feeds_wrapper() -> List[str]:
        return discover_from_feeds(base_url, client)

    def discover_from_html_head() -> List[str]:
        content = fetch_text(base_url, client)
        if not content:
            return []
        sitemap_urls, feed_urls = parse_html_head_links(content, base_url)
        pages: List[str] = []
        visited: Set[str] = set()
        for sm_url in sitemap_urls:
            pages.extend(fetch_sitemap_recursive(sm_url, client, visited))
        for feed_url in feed_urls:
            pages.extend(fetch_feed_with_pagination(feed_url, client))
        return pages

    tasks = {
        "robots": discover_from_robots,
        "sitemap": discover_from_sitemap_direct,
        "llms": discover_from_llms,
        "feeds": discover_from_feeds_wrapper,
        "html_head": discover_from_html_head,
    }

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                urls = future.result()
                all_urls.extend(urls)
                if urls:
                    print(f"[{name}] Found {len(urls)} URLs", file=sys.stderr)
            except Exception as e:
                print(f"[{name}] Failed: {e}", file=sys.stderr)

    return merge_urls_with_md_preference(all_urls)


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------


def crawl(seed_url: str, json_path: str, discover: bool = True, filter_spec: Optional[str] = None) -> None:
    # ensure absolute seed with scheme for initial parsing
    seed_abs = (
        seed_url
        if re.match(r"^https?://", seed_url, re.IGNORECASE)
        else f"https://{seed_url}"
    )
    parsed = urlparse(seed_abs)
    if not parsed.netloc:
        print("Seed URL must include a hostname", file=sys.stderr)
        sys.exit(2)

    seed_domain = strip_www_and_port(parsed.netloc)
    seed_scheme = parsed.scheme.lower() if parsed.scheme else None

    # Determine domain and path prefix for filtering
    if filter_spec:
        # Use custom filter specification
        allowed_domain, seed_path_prefix = parse_filter_spec(filter_spec, seed_domain)
    else:
        # Default behavior: extract path prefix from seed URL
        allowed_domain = seed_domain
        seed_path = normalize_path(parsed.path or "/")
        seed_path_prefix = seed_path if seed_path != "/" else ""

    # Determine per-domain JSON path (e.g., ghuntley-com.json)
    default_name = pathlib.Path(json_path).name
    domain_filename = f"{allowed_domain.replace('.', '-')}.json"
    domain_json_path = (
        domain_filename if default_name == "crawl_map.json" else json_path
    )

    # One-time migration from old crawl_map.json to per-domain store
    if (
        default_name == "crawl_map.json"
        and pathlib.Path(json_path).exists()
        and not pathlib.Path(domain_json_path).exists()
    ):
        old_mapping = load_store(json_path)
        old_mapping = clean_mapping_assets(old_mapping)
        write_store(old_mapping, domain_json_path)

    mapping = load_store(domain_json_path)
    visited: Set[str] = set()
    to_visit: List[str] = []

    seed_canon = canonical_key(seed_abs)
    if not seed_canon:
        print("Unable to canonicalize seed URL", file=sys.stderr)
        sys.exit(2)

    to_visit.append(seed_canon)
    canon_to_sample: Dict[str, str] = {seed_canon: seed_abs}

    pages_fetched = 0

    client = httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": "stupid-simple-crawler/0.1 (+https://example.invalid)"},
    )

    # URL discovery from robots.txt, sitemaps, llms.txt, feeds
    if discover:
        print("Discovering URLs from sitemaps, llms.txt, feeds...", file=sys.stderr)
        base_url = f"{seed_scheme or 'https'}://{allowed_domain}"
        discovered = discover_urls(base_url, client)

        # Filter to allowed domain/path and add to queue
        added = 0
        for canon in discovered:
            if not within_path_prefix(
                f"https://{canon}", allowed_domain, seed_path_prefix
            ):
                continue
            if canon not in visited and canon not in to_visit:
                to_visit.append(canon)
                added += 1
        if added:
            print(f"Added {added} discovered URLs to queue", file=sys.stderr)

    try:
        while to_visit:
            current = to_visit.pop(0)
            if current in visited:
                continue
            visited.add(current)

            # If we already crawled it in previous sessions, expand neighbors without refetching
            if current in mapping:
                for nb in mapping.get(current, []):
                    if not is_page_like_canon(nb):
                        continue
                    if nb not in visited and nb not in to_visit:
                        to_visit.append(nb)
                continue

            urls_to_try = pick_fetch_urls(
                current, canon_to_sample.get(current), seed_scheme
            )
            fetched = fetch_html(urls_to_try, client)
            if not fetched:
                # Record as crawled with zero links to avoid retrying in future runs
                mapping[current] = []
                mapping = clean_mapping_assets(mapping)
                write_store(mapping, domain_json_path)
                continue

            fetch_url, html = fetched
            links = extract_links(html, fetch_url)

            # Heuristics to detect JavaScript-rendered pages that need Playwright
            soup = BeautifulSoup(html, "html.parser")

            # 1. Check noscript tags for JS warnings
            has_noscript_warning = False
            for noscript in soup.find_all("noscript"):
                text = noscript.get_text().lower()
                if "enable javascript" in text or "javascript enabled" in text:
                    has_noscript_warning = True
                    break

            # 2. Check for Next.js or Nuxt.js mount points
            has_nextjs = bool(soup.find("div", id="__next"))
            has_nuxtjs = bool(soup.find("div", id="__nuxt"))

            # 3. Check for empty Vue.js app div
            app_div = soup.find("div", id="app")
            has_empty_vue_app = app_div and not app_div.get_text(strip=True)

            # 4. Check for Angular app-root
            has_angular_root = bool(soup.find("app-root"))

            # 5. Check for framework-specific paths
            has_framework_paths = "/_next/static/" in html or "/_nuxt/" in html

            needs_playwright = (
                has_noscript_warning
                or has_nextjs
                or has_nuxtjs
                or has_empty_vue_app
                or has_angular_root
                or has_framework_paths
                or not links
                or html.count('<script') >= html.count('<div')
            )

            if needs_playwright:
                print(f"Detected JS-rendered page, trying Playwright for: {fetch_url}", file=sys.stderr)
                pw_fetched = fetch_html_playwright(fetch_url)
                if pw_fetched:
                    _, html = pw_fetched
                    links = extract_links(html, fetch_url)
                    if links:
                        print(f"Playwright found {len(links)} links!", file=sys.stderr)

            out_neighbors: Set[str] = set()
            for link in links:
                if not within_path_prefix(link, allowed_domain, seed_path_prefix):
                    continue
                c = canonical_key(link)
                if not c:
                    continue
                if not is_page_like_canon(c):
                    continue
                out_neighbors.add(c)
                if c not in canon_to_sample:
                    canon_to_sample[c] = link

            mapping[current] = sorted(out_neighbors)
            mapping = clean_mapping_assets(mapping)
            write_store(mapping, domain_json_path)
            pages_fetched += 1

            for nb in mapping[current]:
                if nb not in visited and nb not in to_visit:
                    to_visit.append(nb)
    finally:
        client.close()

    # Produce a concise summary and list all known nodes
    all_nodes: Set[str] = set(mapping.keys())
    for vs in mapping.values():
        all_nodes.update(vs)

    print(f"Allowed domain: {allowed_domain}")
    if seed_path_prefix:
        print(f"Path filter: {seed_path_prefix}")
    else:
        print("Path filter: (none - whole domain)")
    print(f"Unique same-domain URLs known: {len(all_nodes)}")
    print(f"Pages fetched this run: {pages_fetched}")
    print(f"Graph saved to: {domain_json_path}")
    for node in sorted(all_nodes):
        print(node)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stupid-simple same-domain crawler with JSON memoization."
    )
    parser.add_argument("url", help="Seed URL, e.g., https://example.com")
    parser.add_argument(
        "-j",
        "--json",
        default="crawl_map.json",
        help="Path to JSON map file (default: ./crawl_map.json)",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip URL discovery from robots.txt, sitemaps, llms.txt, feeds",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=(
            "Optional domain/path filter (overrides default path extraction from seed URL). "
            "Supports: full URLs (https://example.com/path), domain+path (example.com/path), "
            "or bare paths (path/to/filter or /path/to/filter). "
            "If unspecified, filters to seed URL's path."
        ),
    )
    args = parser.parse_args()
    crawl(args.url, args.json, discover=not args.no_discover, filter_spec=args.filter)


if __name__ == "__main__":
    main()
