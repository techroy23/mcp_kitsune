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