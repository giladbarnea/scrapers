# JS-injected nav detection in needs_javascript (2026-06-12)

**Trigger.** `mc https://zunit.xyz/docs/` returned a single page while the site has eleven
doc pages. Root cause was a blind spot, not a crawl bug: the site's sidebar nav ships as
empty `<ul>` placeholders that client-side JS fills in. `map_crawl.fetch_links_for_canon`
has a Playwright fallback for exactly this, but neither of its two triggers fired —
`needs_javascript` only knew SPA framework markers (Next/Nuxt/Vue/Angular, script ratio),
and the "no links found" backup was defeated by the page's few static header/footer links.
`rf` shared the blind spot since both consume `fetch_utils.needs_javascript`.

**Decisions.**
- Fixed in the shared helper rather than in map_crawl's trigger expression, so both `mc`
  and `rf` benefit; the new check follows the helper's existing flat-list-of-signals shape.
- Threshold of ≥2 empty lists: a false positive costs a full Playwright browser launch per
  page (the fallback launches and tears down a browser every call), so a lone empty `<ul>`
  (cart widget, comments stub) shouldn't trip it. The motivating page has twelve.
- Dropped the silent `except ImportError: return False` around the bs4 import — bs4 is
  pinned in pyproject.toml and both entry scripts' uv frontmatter, so the guard could only
  mask a broken environment.

**Known debt.** The per-call browser launch in `fetch_html_with_playwright` now bites more
often (every page of a JS-nav site pays startup cost). Reusing a browser across the crawl
is nontrivial: callers run inside a ThreadPoolExecutor and Playwright's sync API is not
thread-safe, so it needs per-thread lifecycles or lock-guarded contexts. Deliberately
deferred.

**Verification.** Live re-crawl found all 11 `/docs/*` pages (was 1); regression tests in
`tests/test_fetch_utils.py`.
