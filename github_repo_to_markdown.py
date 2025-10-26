#!/usr/bin/env python3
"""
Context Preparer for "humanlayer" workflow research.

This version uses the GitHub REST API via the `requests` library (no `gh` CLI).

It will:
  1) Fetch the overview file from `humanlayer/advanced-context-engineering-for-coding-agents@main:ace-fca.md`.
  2) Recursively list and scrape specific directories from `ai-that-works/ai-that-works@main`.
  3) Additionally, read all files under `.claude/commands` within `ai-that-works/ai-that-works`.

Each file's content is printed to standard output wrapped **exactly** as:

  <file path: owner/repo/path-in-repo>
  ...file contents...
  </file>

(There is intentionally **no** CDATA wrapper.)

Assumptions:
- Environment variable `GITHUB_API_KEY` is a GitHub Personal Access Token with at least
  `public_repo` scope for public repositories.
- Default branches are `main` for both repositories.

Notes:
- Skips binary and very large files (> 2 MB by default).
- Includes exponential backoff for transient network failures and rate limiting
  (HTTP 429/403 with rate limit exhaustion).
- Without CDATA, if a file contains the literal string `</file>`, the outer wrapper could be
  confused by naive parsers. This script follows your requirement as-is.
"""

from __future__ import annotations

import os
import sys
import time
import json
from typing import Dict, List, Optional

import requests

# --------------------------- Configuration --------------------------- #
# Allow-list of documentation extensions (normalized, no leading dot). Empty string means extensionless files.
WHITELIST_EXTS = {"md", "mdx", "txt", "rst", "rtf", ""}
OVERVIEW = {
    "owner": "humanlayer",
    "repo": "advanced-context-engineering-for-coding-agents",
    "ref": "main",
    "path": "ace-fca.md",
}

TARGET_REPO = {
    "owner": "ai-that-works",
    "repo": "ai-that-works",
    "ref": "main",
}

TARGET_DIRS: List[str] = [
    "2025-06-24-ai-content-pipeline",
    "2025-07-01-ai-content-pipeline-2",
    "2025-07-08-context-engineering",
    "2025-08-05-advanced-context-engineering-for-coding-agents",
    "thoughts/shared",
    ".claude/commands",
]

MAX_BYTES = 2_000_000  # 2 MB guardrail
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.4
TIMEOUT = 30  # seconds per request

GITHUB_API = "https://api.github.com"
# !!! WARNING: Hardcoded token per user request. Consider rotating this token after use.
TOKEN = os.getenv("GITHUB_API_KEY")

# --------------------------- HTTP helpers --------------------------- #


def _headers() -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "context-prep-script/1.0",
    }
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    raw_accept: bool = False,
) -> requests.Response:
    """HTTP request with retries and basic rate-limit handling."""
    merged_headers = _headers()
    if headers:
        merged_headers.update(headers)
    if raw_accept:
        merged_headers["Accept"] = "application/vnd.github.raw"

    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url, headers=merged_headers, params=params, timeout=TIMEOUT
            )
            # Rate limiting
            if (
                resp.status_code in (429, 403)
                and resp.headers.get("X-RateLimit-Remaining") == "0"
            ):
                reset = resp.headers.get("X-RateLimit-Reset")
                if reset and reset.isdigit():
                    wait_s = max(0, int(reset) - int(time.time())) + 1
                else:
                    wait_s = BACKOFF_BASE_SECONDS**attempt
                time.sleep(min(wait_s, 60))
                continue
            if 200 <= resp.status_code < 300:
                return resp
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(BACKOFF_BASE_SECONDS**attempt)
                continue
            resp.raise_for_status()
        except Exception as e:
            last_exc = e
            if attempt == MAX_RETRIES:
                raise
            time.sleep(BACKOFF_BASE_SECONDS**attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("Unexpected request failure without exception.")


# --------------------------- GitHub API helpers --------------------------- #


def list_directory(owner: str, repo: str, ref: str, path: str):
    """List a path with the Contents API; may return a list (dir) or dict (file)."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    resp = _request("GET", url, params={"ref": ref})
    try:
        return resp.json()
    except json.JSONDecodeError:
        return []


def walk_paths(owner: str, repo: str, ref: str, root: str) -> List[str]:
    """Recursively collect file paths under `root`."""
    files: List[str] = []

    def _recurse(p: str) -> None:
        data = list_directory(owner, repo, ref, p)
        if isinstance(data, dict) and data.get("type") == "file":
            files.append(data.get("path", p))
            return
        if not isinstance(data, list):
            return
        for ent in data:
            etype = ent.get("type")
            epath = ent.get("path") or ent.get("name")
            if not epath:
                continue
            if etype == "dir":
                _recurse(epath)
            elif etype == "file":
                files.append(epath)
            # ignore symlinks/submodules

    _recurse(root)
    return files


def get_file_extension(path: str) -> str:
    """Return normalized extension without leading dot; '' if extensionless.

    Examples:
      - 'README.MD' -> 'md'
      - 'notes.txt' -> 'txt'
      - 'archive.rst' -> 'rst'
      - 'Makefile' -> ''
      - '.gitignore' -> '' (treated as extensionless)
    """

    # os.path.splitext handles multi-dots and hidden files correctly
    _, ext = os.path.splitext(path)
    if not ext:
        return ""
    # Remove a single leading dot and lowercase
    return ext[1:].lower()


def fetch_file_text(owner: str, repo: str, ref: str, path: str) -> Optional[str]:
    """Fetch file bytes via raw accept and decode to text. Returns None for oversized or binary files."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    resp = _request("GET", url, params={"ref": ref}, raw_accept=True)
    content = resp.content

    if len(content) > MAX_BYTES:
        return None
    if b"\x00" in content:
        return None
    for enc in ("utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


# --------------------------- Output helpers --------------------------- #


def print_wrapped(owner: str, repo: str, path: str, text: str) -> None:
    """Print content exactly as: <file path: owner/repo/path> ... </file>"""
    sys.stdout.write(f"<file path: {owner}/{repo}/{path}>\n")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("</file>\n")


# --------------------------- Main --------------------------- #


def main() -> None:
    # 1) Overview
    ov = OVERVIEW
    overview_text = fetch_file_text(ov["owner"], ov["repo"], ov["ref"], ov["path"])
    if overview_text is not None:
        print_wrapped(ov["owner"], ov["repo"], ov["path"], overview_text)
    else:
        print(
            f"<!-- Skipped overview {ov['owner']}/{ov['repo']}/{ov['path']} -->",
            file=sys.stderr,
        )

    # 2) Target repo directories (including .claude/commands)
    t = TARGET_REPO
    all_paths: List[str] = []
    for d in TARGET_DIRS:
        data = list_directory(t["owner"], t["repo"], t["ref"], d)
        if isinstance(data, list) and data:
            all_paths.extend(walk_paths(t["owner"], t["repo"], t["ref"], d))
        elif isinstance(data, dict) and data.get("type") == "file":
            all_paths.append(data.get("path", d))
        else:
            print(
                f"<!-- Directory not found or empty: {t['owner']}/{t['repo']}/{d} -->",
                file=sys.stderr,
            )

    seen = set()
    for p in sorted(all_paths):
        if p in seen:
            continue
        seen.add(p)
        # Filter by documentation whitelist
        ext = get_file_extension(p)
        if not (ext == "" or ext in WHITELIST_EXTS):
            continue
        txt = fetch_file_text(t["owner"], t["repo"], t["ref"], p)
        if txt is None:
            continue
        print_wrapped(t["owner"], t["repo"], p, txt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
