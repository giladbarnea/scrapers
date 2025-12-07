# Next Steps

1. github_repo_to_markdown.py is hardcoded to advanced-context-engineering, should be generic.
2. github_repo_to_markdown.py doesn't wrap in xml-like tags
3. map_crawl.py should integrate with github_repo_to_markdown.py?
4. Parse site-mapping files like llms.txt.


## robots.txt, llms.txt etc

#### The Big Three

1. **`/sitemap.xml`** — The OG, standardized by Google. Often linked from robots.txt. Can point to multiple sitemaps (sitemap index files)
2. **`/robots.txt`** — Not a URL list itself, but often contains `Sitemap:` directives pointing to sitemaps
3. **`/llms.txt`** — The new kid, specifically for LLMs to understand site structure. Still emerging

#### Other useful ones

4. **`/sitemap_index.xml`** — Common alternative location for sitemap indexes
5. **`/feed/`**, **`/rss/`**, **`/atom.xml`**, **`/feed.xml`** — RSS/Atom feeds. WordPress uses `/feed/`, others vary
6. **`/.well-known/`** — A whole directory of standardized files (security.txt, etc.). Not URL lists per se, but useful metadata
7. **`/robots.txt` + crawl** — Sometimes the `Disallow` rules inadvertently reveal interesting paths

Tip: Add `?page=1` or look for pagination on archive pages.

#### robots.txt: programmatic discovery

Fetch `/robots.txt`, parse for `Sitemap:` lines, then recursively fetch those (since sitemaps can nest via `<sitemapindex>`, or otherwise, or not at all -- keep an open mind).