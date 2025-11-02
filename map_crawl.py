#!/usr/bin/env uv run
# /// script
# dependencies = [
#     "httpx",
#     "beautifulsoup4",
#     "playwright",
# ]
# ///

"""
I’ll outline reliable metadata sources and a concrete, non-ML graph approach you can implement quickly.

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
"""

import argparse
import contextlib
import json
import pathlib
import posixpath
import re
import sys
from typing import Dict, List, Optional, Set, Tuple
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

    path = p.path or "/"
    # collapse duplicate slashes
    path = re.sub(r"/+", "/", path)
    # normalize dot segments
    try:
        path = posixpath.normpath(path)
    except Exception:
        # fallback: keep as-is if normpath fails for any reason
        pass
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")

    if path == "/":
        return host
    return f"{host}{path}"


def same_domain(url: str, allowed_domain: str) -> bool:
    p = urlparse(url)
    if not p.netloc:
        return False
    return strip_www_and_port(p.netloc) == allowed_domain


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


def crawl(seed_url: str, json_path: str) -> None:
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

    allowed_domain = strip_www_and_port(parsed.netloc)
    seed_scheme = parsed.scheme.lower() if parsed.scheme else None

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
                if not same_domain(link, allowed_domain):
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
    args = parser.parse_args()
    crawl(args.url, args.json)


if __name__ == "__main__":
    main()
