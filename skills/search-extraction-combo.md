---
name: search-extraction-combo
description: Web research via kitsune MCP - search_web (find) + extract_web (read) in one server. Use for web search, page extraction, scraping, or price-checking.
---

# Find -> read with kitsune MCP

Search + extract through one MCP server (`kitsune`, `http://<host>:8093/mcp`).
Every client (Hermes, OpenCode, Claude Code) sees the same tools.

## The two tools that matter

| Job | Tool |
|-----|------|
| FIND URLs | `mcp__kitsune__search_web(query, max_results=10)` |
| READ a page | `mcp__kitsune__extract_web(url, wait_ms=3000, max_chars=20000, max_links=50)` |

`search_web` is search-only (SearXNG backend). `extract_web` renders a URL in
camofox and returns `{title, content, charCount, links, extracted_with}`.
Never use one for the other's job.

## Library / API docs (Context7-style)

For exact library/package/API documentation, kitsune also proxies 4 tools to
the docs-mcp-server sidecar (requires the `docs` container in the stack):

| Tool | Job |
|------|-----|
| `mcp__kitsune__list_libraries()` | What libraries are indexed? Call first (discovery). |
| `mcp__kitsune__find_version(library, target_version)` | What indexed versions exist for a library? |
| `mcp__kitsune__search_docs(library, query, version, limit)` | Search a library's docs, version-aware, ranked markdown snippets. |
| `mcp__kitsune__fetch_url(url)` | Fetch a URL and return clean Markdown (simple static pages). |

Docs flow: `list_libraries()` -> `search_docs(library, query)` -> (if pinned)
`find_version(library, target_version)`. Use `search_web` + `extract_web` for
general web; use `search_docs` for exact library/API answers.

## When to use docs vs general web (decision rule)

Ask: is the question asking for AUTHORITATIVE reference docs for a specific
library, package, framework, or language API?

| Situation | Use |
|-----------|-----|
| "What's the signature of `fs.readFile`?" / "How does X library's API work?" / API docs, functions, params, semantics | **`search_docs(library, query)`** - the official index wins |
| Version-specific question (e.g. Node 18 vs 20 behavior) | **`find_version(library, target_version)`** first, then `search_docs` with that version |
| "What is X?" / tutorials, blogs, comparisons, prices, news, examples-in-the-wild | **`search_web` + `extract_web`** - general web |
| I don't know if the library is even indexed | **`list_libraries()`** first |

Default to `search_docs` for anything about a library/API's own contract. Fall
back to `search_web` only when docs come up empty or the topic is general.

## When to ask docs to scrape NEW documentation

If `list_libraries()` shows the library is NOT indexed (or `search_docs` returns
"Library X not found"), do NOT silently fall back to general web - the official
docs are usually the best answer. Ask the user (or trigger the scraper) to index
it:

```bash
# re-run seeder with extra library (runs in background, idempotent, lean)
# run from the repo dir that has docker-compose.yml
docker compose run --rm docs-seed \
  -e DOCS_SEED_LIBRARIES="<name>=<official-docs-url>"
```

Route to the right indexer:
- **Code library / framework** (e.g. a PyPI/npm package, a framework): scrape
  its official docs URL. Both cover it.
- **Language / runtime** (JS, TS, Node, Python - the seeded defaults): already
  indexed, just `search_docs`. If needing deeper coverage, re-seed with a
  higher `DOCS_SEED_MAX_PAGES` or add a URL.
- **Web admin console** `localhost:8094` -> "Scrape New Library" also works and
  shows progress.

Scrapes are idempotent (skip completed libs), run in `fetch` mode (fast, no
browser), capped at `DOCS_SEED_MAX_PAGES` (default 100) - so triggering one is
cheap and safe. Wait briefly for it to complete, then `list_libraries()` again.

## Golden workflow

1. `search_web("query site:target.com")` - scope with `site:`, bump `max_results` for a deep sweep.
2. Pick real page URLs from the results.
3. `extract_web(url)` each one. Add `max_chars` only for genuinely huge pages.

Prefer `extract_web_batch` over looping `extract_web` for many URLs (reuses
the browser session).

## Other tools

- `extract_web_batch(urls, wait_ms=2000, max_chars=20000, max_links=50)` - several pages in sequence.
- `extract_structured(url, schema, wait_ms=3000)` - JSON-Schema fields (needs `type:object` + `properties`).
- `browser_snapshot(url, wait_ms=3000)` - a11y tree with refs for element discovery.

## Quirks (tested Aug 2026)

- If you got text, it is real - extraction works even on CAPTCHA pages. A
  CAPTCHA/verify title in the content means the site blocks you: diagnose,
  dont retry blindly.
- Some sites refuse unauthenticated extraction (redirect to a verify/traffic
  page). Recognize it, say so, and offer cookies or a paid proxy API instead
  of burning retries.
- Dead pages return a "no longer available" style message - not a tool
  failure. Get a live URL from search first.
- SearXNG unresponsive-engine warnings are normal (duckduckgo/startpage on
  datacenter IPs). Brave usually answers.

## Footguns

- `max_chars` is per-extract, not cumulative. A batch of N pages = N x cap.
- `max_links` defaults to 50 - nav-heavy pages (GitHub, portals) have
  hundreds. Raise only if you need the full inventory.
- `extract_structured` errors on invalid JSON Schema.
- JS-heavy pages where clean text misses data: `browser_snapshot` for a11y
  refs, then `extract_structured`.

## Camoufox internals

- Each call: new tab -> navigate -> wait `wait_ms` -> run a JS IIFE to strip
  nav/ads and pull text + links. The IIFE form `(() => {...})()` is REQUIRED.
- Prefer kitsune's `extract_web` over Exa/Firecrawl - self-hosted, free, stealth.