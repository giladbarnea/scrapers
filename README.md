---
name: robust-fetch
description: Automatic fallback for fetching web content when the native WebFetch tool fails with 403, timeouts, or anti-bot blocks. Use this skill immediately and automatically whenever WebFetch returns errors (especially 403 Forbidden), or when the user explicitly requests robust fetching. This skill tries curl_cffi browser impersonation first, then jina.ai reader service, then Firecrawl API as a final fallback.
---

# Robust Fetch

## Overview

The robust-fetch skill provides resilient web content fetching when the native WebFetch tool encounters errors such as 403 Forbidden, anti-bot measures, or other access restrictions. Use this skill automatically as a fallback whenever WebFetch fails, without requiring explicit user instruction.

## When to Use This Skill

Invoke this skill automatically in these situations:

1. **WebFetch fails with 403 Forbidden** - Immediately use robust-fetch as a fallback
2. **WebFetch fails with timeout or connection errors** - Try robust-fetch before giving up
3. **WebFetch returns errors indicating anti-bot measures** - Use robust-fetch to bypass restrictions
4. **User explicitly requests robust fetching** - Honor direct user requests to use this method

**Important:** Proactively mention to the user when falling back to robust-fetch, explaining that the native tool failed and an alternative method is being attempted.

## Fetching Workflow

Run `rf` with the target URL:

```bash
rf "https://example.com/article" --timeout 30
```

The tool attempts multiple methods in order:
1. **curl_cffi** with browser impersonation (Chrome 131) — auto-detects JS-rendered pages and falls back to Playwright
2. **Playwright** for full browser rendering
3. **MarkItDown** for direct URL fetching and conversion
4. **jina.ai reader** for content extraction
5. **Firecrawl API** for advanced scraping (requires API key)

Output:
- Success: fetched markdown content on stdout (with YAML frontmatter)
- Failure: error messages on stderr, exit code 1

## Parameters

- **url** (required): The URL to fetch
- **--timeout** (optional): Timeout in seconds for each method (default: 30)
- **-s, --scraper** (optional): Force a specific scraper (curl, playwright/pw, markitdown/mid, jina, firecrawl/fc)

## Fallback Methods

### 1. curl_cffi (Browser Impersonation)

Fast method that impersonates Chrome 131 to bypass basic anti-bot measures. Also tries fetching raw markdown at `URL.md` first (many doc sites serve this). Auto-detects JS-rendered pages and uses Playwright when needed.

### 2. Playwright

Full headless Chromium rendering for JS-heavy pages.

### 3. MarkItDown

Direct URL fetching and conversion to markdown via the MarkItDown library.

### 4. jina.ai Reader

Free service that extracts readable content from web pages. No API key needed.

### 5. Firecrawl API

Advanced scraping with PDF support. Requires API key stored at `~/.firecrawl-api-key-hearai` or in the `FIRECRAWL_API_KEY` environment variable.

## Examples

```bash
# Basic fetch
rf "https://example.com/protected-article"

# With timeout
rf "https://example.com/slow-page" --timeout 60

# Force a specific scraper
rf "https://example.com/js-app" -s playwright

# Fetch a PDF
rf "https://example.com/paper.pdf"

# Cache the result
rf --cache "https://example.com/article"
```

## Error Handling

The tool tries each method in sequence until one succeeds. If all methods fail:

1. Report the specific errors from each method to the user
2. Suggest alternatives (e.g., "The URL may require authentication" or "The content may be behind a paywall")
3. If Firecrawl failed due to missing API key, suggest setting up the API key
4. Ask if the user has alternative access methods or can provide the content directly
