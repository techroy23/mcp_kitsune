---
name: kitsune-mcp-usage
description: "Complete guide to kitsune MCP tools — search, extract, scrape, debug. Includes web research workflow, docs lookup, Camoufox persistence fix, and common pitfalls."
version: 1.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kitsune, mcp, troubleshooting, search_web, search_docs, searxng, camoufox, persistence, web-research, scraping]
---

# Kitsune MCP Usage Guide

Use when: Calling any kitsune MCP tool via `tool_call`. Covers tool reference, web research workflows, docs lookup, and troubleshooting including the Camoufox persistence corruption bug fix.

## Overview

Kitsune MCP exposes one server (`kitsune`, `http://<host>:8093/mcp`) with 9 tools across two backends:

| Backend | Tools | Purpose |
|---------|-------|---------|
| SearXNG | `search_web` | General web search |
| docs-mcp-server | `list_libraries`, `find_version`, `search_docs`, `fetch_url` | Library/API documentation lookup |
| Camoufox browser | `extract_web`, `extract_web_batch`, `extract_structured`, `browser_snapshot` | JS rendering, structured extraction, a11y snapshots |

Every client (Hermes, OpenCode, Claude Code) sees the same tools.

## Golden Workflow

### General web research
1. `search_web("query site:target.com")` — scope with `site:`, bump `max_results` for deep sweeps
2. Pick real page URLs from the results
3. `extract_web(url)` each one — add `max_chars` only for genuinely huge pages
4. Prefer `extract_web_batch` over looping `extract_web` for many URLs (reuses the browser session)

### Library / API docs (Context7-style)
For exact library/package/API documentation, use the docs-mcp-server sidecar (requires the `docs` container in the stack):

| Tool | Job |
|------|-----|
| `mcp__kitsune__list_libraries()` | What libraries are indexed? Call first (discovery). |
| `mcp__kitsune__find_version(library, target_version)` | What indexed versions exist for a library? |
| `mcp__kitsune__search_docs(library, query, version, limit)` | Search a library's docs, version-aware, ranked markdown snippets. |
| `mcp__kitsune__fetch_url(url)` | Fetch a URL and return clean Markdown (simple static pages). |

Docs flow: `list_libraries()` → `search_docs(library, query)` → (if pinned) `find_version(library, target_version)`.

### Docs vs general web (decision rule)
Ask: is the question asking for AUTHORITATIVE reference docs for a specific library, package, framework, or language API?

| Situation | Use |
|-----------|-----|
| "What's the signature of `fs.readFile`?" / "How does X library's API work?" / API docs, functions, params, semantics | **`search_docs(library, query)`** — the official index wins |
| Version-specific question (e.g. Node 18 vs 20 behavior) | **`find_version(library, target_version)`** first, then `search_docs` with that version |
| "What is X?" / tutorials, blogs, comparisons, prices, news, examples-in-the-wild | **`search_web` + `extract_web`** — general web |
| I don't know if the library is even indexed | **`list_libraries()`** first |

Default to `search_docs` for anything about a library/API's own contract. Fall back to `search_web` only when docs come up empty or the topic is general.

## Tool Reference

### `mcp__kitsune__search_docs` — Documentation search
Searches indexed documentation for a specific library. Returns ranked markdown snippets.

**Required args:** `library` (string), `query` (string)
**Optional args:** `version`, `limit` (int)

❌ Wrong: `query` only — fails with missing `library` error
✅ Right:
```json
[
  {
    "name": "mcp__kitsune__search_docs",
    "arguments": { "library": "python", "query": "requests", "limit": 3 }
  }
]
```

### `mcp__kitsune__list_libraries` — Discover indexed docs
Lists libraries available for `search_docs`. No args needed.

✅ Pattern: Call first to discover valid library names before using `search_docs`.

### `mcp__kitsune__search_web` — Web search via SearXNG
Searches the web via the self-hosted SearXNG backend.

**Required args:** `query` (string)
**Optional args:** `categories`, `engines`, `language`, `pageno`, `max_results`

⚠️ Known issue: If SearXNG container is down, returns `search failed: ` (empty error). Check container status before retrying.

### `mcp__kitsune__fetch_url` — Web page fetch
Fetches a URL and converts to clean Markdown (simple static pages, no JS execution).

**Required args:** `url` (string)

⚠️ Known issue: If the docs sidecar is down, returns `docs fetch failed: ` (empty error).

### `mcp__kitsune__extract_web` — Page extraction with JS
Renders a URL in a browser (Camoufox) and extracts content. Better for JS-heavy pages than `fetch_url`.

**Required args:** `url` (string)
**Optional args:** `wait_ms` (default 3000), `max_chars` (default 20000), `max_links` (default 50)

⚠️ **Camoufox profile corruption bug:** If the container has been running with intermittent tab-creation timeouts, the persisted profile state at `/root/.camofox/profiles/*` becomes corrupted, causing cascading `extract_*` failures. Symptoms in logs: `tab create timed out after 30000ms`, `new page timed out after 10000ms`, `new_page_unresponsive`, `HTTP 500: Tab not found`.

**Fix:** The server patch now handles this automatically. Set `CAMOUFOX_DISABLE_PERSISTENCE=true` in the `mcp` service's environment in `docker-compose.yml` to generate a unique session key per request, bypassing corrupted profile state entirely. The `_with_retry()` wrapper in `server.py` handles session resets on HTTP 500/404 automatically (up to 3 attempts).

If you need to clear manually (for debugging):
```bash
docker exec mcpkitsune-camofox rm -rf /root/.camofox/profiles/*
docker restart mcpkitsune-camofox
```
This is safe — Camoufox auto-recreates profiles on the next request.

### `mcp__kitsune__extract_web_batch` — Batch page extraction
Extracts multiple URLs in sequence (reuses browser session).

**Required args:** `urls` (array of strings)
**Optional args:** `wait_ms`, `max_chars`, `max_links`

### `mcp__kitsune__extract_structured` — Structured data extraction
Extracts specific fields from a page using JSON Schema.

**Required args:** `url` (string), `schema` (object with `type: "object"` + `properties`)
**Optional args:** `wait_ms`

⚠️ The schema MUST have top-level `type: "object"` — Camoufox's extractor returns HTTP 400 otherwise.

### `mcp__kitsune__browser_snapshot` — Accessibility snapshot
Returns the accessibility tree of a page for element discovery (useful when you need a11y refs but `extract_web` misses data).

**Required args:** `url` (string)
**Optional args:** `wait_ms`

## Scraping New Documentation

If `list_libraries()` shows the library is NOT indexed (or `search_docs` returns "Library X not found"), do NOT silently fall back to general web — the official docs are usually the best answer. Ask the user (or trigger the scraper) to index it:

```bash
# re-run seeder with extra library (runs in background, idempotent, lean)
# run from the repo dir that has docker-compose.yml
docker compose run --rm docs-seed \
  -e DOCS_SEED_LIBRARIES="<name>=<official-docs-url>"
```

Route to the right indexer:
- **Code library / framework** (e.g. a PyPI/npm package, a framework): scrape its official docs URL
- **Language / runtime** (JS, TS, Node, Python — the seeded defaults): already indexed, just `search_docs`. If needing deeper coverage, re-seed with a higher `DOCS_SEED_MAX_PAGES` or add a URL
- **Web admin console** `localhost:8094` → "Scrape New Library" also works and shows progress

Scrapes are idempotent (skip completed libs), run in `fetch` mode (fast, no browser), capped at `DOCS_SEED_MAX_PAGES` (default 100) — so triggering one is cheap and safe. Wait briefly for it to complete, then `list_libraries()` again.

**Trigger pitfall (tested):** A full scrape takes 4-5+ min. A 180s foreground `terminal()` call WILL time out. Run the trigger with `background=true` + `notify_on_complete=true`, or background it and poll `list_libraries()` / the seed container. The scrape keeps running even if your foreground wrapper times out.

**Seed coverage (tested):** The 100-page default cap is LEAN — on big docs sites (e.g. python.org) it can grab navigation/index pages and miss the deep API pages (asyncio.gather returned bdb/genindex pages, not the real asyncio-task page). If a `search_docs` hit looks wrong/missing for a language stdlib, re-seed that library deeper:

```bash
# force re-scrape of a completed lib with a deeper crawl
docker compose run --rm -e DOCS_SEED_MAX_PAGES=400 \
  -e DOCS_SEED_LIBRARIES="python=https://docs.python.org/3/@fetch" \
  -e DOCS_SEED_FORCE=1 docs-seed
```

## Troubleshooting

- **Missing required argument error**: Use `tool_describe` to get the exact schema
- **Empty `search failed:` error from `search_web`**: SearXNG backend is down — check containers
- **Library not found in `search_docs`**: Call `list_libraries` first to discover valid names
- **`extract_web` returning `Camoufox API POST /tabs -> HTTP 500`**: Camoufox profile corruption — the server's `_with_retry()` should handle this automatically by resetting the session. If it still fails, ensure `CAMOUFOX_DISABLE_PERSISTENCE=true` is set in `docker-compose.yml` for the `mcp` service, then clear `/root/.camofox/profiles/*` and restart container (see Camoufox bug above)
- **`fetch_url` returning `docs fetch failed:`**: docs sidecar container is down or unresponsive — check `docker ps` and container health
- **`extract_structured` returning HTTP 400**: Schema is invalid — must be `{"type": "object", "properties": {...}}`

## Footguns (tested)

- **`max_chars`** is per-extract, not cumulative. A batch of N pages = N × cap.
- **`max_links`** defaults to 50 — nav-heavy pages (GitHub, portals) have hundreds. Raise only if you need the full inventory.
- **`extract_structured`** errors on invalid JSON Schema.
- **JS-heavy pages** where clean text misses data: use `browser_snapshot` for a11y refs, then `extract_structured`.
- If you got text, it is real — extraction works even on CAPTCHA pages. A CAPTCHA/verify title in the content means the site blocks you: diagnose, don't retry blindly.
- Some sites refuse unauthenticated extraction (redirect to a verify/traffic page). Recognize it, say so, and offer cookies or a paid proxy API instead of burning retries.
- Dead pages return a "no longer available" style message — not a tool failure. Get a live URL from search first.
- SearXNG unresponsive-engine warnings are normal (duckduckgo/startpage on datacenter IPs). Brave usually answers.

## Camoufox Internals

- Each call: new tab → navigate → wait `wait_ms` → run a JS IIFE to strip nav/ads and pull text + links. The IIFE form `(() => {...})()` is REQUIRED.
- Prefer kitsune's `extract_web` over Exa/Firecrawl — self-hosted, free, stealth.

## Smoke Test Results (PH time 2026-09-22)

| Tool | Status | Result |
|------|--------|--------|
| `search_web` | ✅ Works | Returned 10 real results for "test" (speedtest.net, merriam-webster, etc.) |
| `list_libraries` | ✅ Works | Returns: js, node, python, ts |
| `find_version` | ✅ Works | Returns version info for indexed libs |
| `search_docs` | ✅ Works | Returns ranked doc snippets (Python asyncio docs) |
| `extract_web` | ✅ Works (after fix) | Extracted example.com, xda-developers.com successfully |
| `extract_web_batch` | ✅ Works | Multi-URL extraction with session reuse |
| `extract_structured` | ✅ Works (schema issue only) | Tab/extract works; HTTP 400 only on invalid JSON schema (type:object required) |
| `browser_snapshot` | ✅ Works | Returns a11y tree with element refs |
| `fetch_url` | ✅ Works | Returns clean Markdown (static pages) |
