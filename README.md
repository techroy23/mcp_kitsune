# mcp_kitsune - self-contained search + extract for AI agents

<center><img src="image.png" width="250" /></center>


A Docker Compose stack that gives AI agents (and scripts) two things they
usually cannot get with a plain HTTP library: **search results that are not
CAPTCHA-blocked** and **page text from sites that block scrapers**. It runs
locally, exposes everything over one MCP endpoint, and works with Hermes,
Claude Code, and OpenCode out of the box.

## What is this?

- [SearXNG](https://github.com/searxng/searxng) does the **find** part - a
  metasearch engine that queries many engines server-side, so results come
  back even from a datacenter IP.
- [Camoufox](https://github.com/jo-inc/camofox-browser) does the **read**
  part - a patched Firefox with anti-detection fingerprinting, so
  Cloudflare-protected and JS-heavy pages render like they would in a real
  browser.
- `kitsune` (the MCP server) ties them together over [MCP Streamable
  HTTP](https://modelcontextprotocol.io), so any MCP client can call search +
  extract as plain tools.
- [docs-mcp-server](https://github.com/arabold/docs-mcp-server) adds a
  **library documentation index** - search exact-version API docs (React,
  TypeScript, whatever you index) with semantic ranking. A self-hosted
  replacement for Context7.

One command starts all five containers (browser, search, cache, docs index,
MCP gateway). Everything stays on your machine.

## Quick start

Requires Docker with the Compose plugin. About 5 GB free disk (docs sidecar
image is ~2.7 GB).

```bash
git clone https://github.com/techroy23/mcp_kitsune.git
cd mcp_kitsune
./install.sh            # build + start + verify (asks about a skill file)
```

Or skip the helper and run compose directly:

```bash
docker compose up -d --build
```

First build downloads ~300 MB (Camoufox browser + yt-dlp) and takes 3-5 min.
Later starts are instant.

## Verify it is working

```bash
docker compose ps
# all five containers should show "Up (healthy)"

curl -s http://localhost:8093/health
# {"status":"ok","camoufox":{"ok":true,"browserConnected":true,...},"docs":{"ok":true,...},...}
```

`camoufox.ok: true` means the MCP server can reach the browser. `docs.ok:
true` means the docs index sidecar is up and queryable. Stop with
`docker compose down`, start again with `docker compose up -d`.

## Using it from an MCP client

The stack exposes one endpoint: `http://<host>:8093/mcp` (Streamable HTTP).
The registration command differs per client; the endpoint is the same. Use
`localhost` when the client runs on the same machine. To reach it from
another machine, set `BIND_IP` (see "Binding to the network" above) and use
that machine's LAN IP.

### Hermes

```bash
hermes mcp add kitsune --transport streamable-http --url http://localhost:8093/mcp
```

### Claude Code

```bash
claude mcp add --transport http kitsune http://localhost:8093/mcp
```

Or just open the repo - the committed `.mcp.json` is picked up automatically.

### OpenCode

```bash
# add to ~/.config/opencode/opencode.jsonc under "mcp":
#   "kitsune": { "type": "remote", "url": "http://localhost:8093/mcp", "enabled": true }
```

Or open the repo - `opencode.jsonc` is committed and auto-detected.

## Tools

| Tool | What it does |
|------|--------------|
| `search_web(query, ...)` | Searches the web through SearXNG, returns results |
| `extract_web(url, wait_ms=3000, max_chars=20000, max_links=50)` | Renders a page in Camoufox, returns clean text |
| `extract_web_batch(urls, ...)` | Same, for several pages in sequence |
| `extract_structured(url, json_schema)` | Pulls fields out of a page using a JSON schema |
| `browser_snapshot(url)` | Accessibility snapshot + screenshot of a page |
| `search_docs(library, query, version, limit)` | Searches the indexed library docs (docs-mcp-server sidecar). Version-aware, returns ranked markdown snippets |
| `fetch_url(url)` | Fetches a URL and returns clean Markdown (docs-mcp-server sidecar) |
| `list_libraries()` | Lists the libraries currently indexed in the docs sidecar (discovery before search_docs) |
| `find_version(library, target_version)` | Finds the best matching indexed version for a library (version-aware) |

`search_docs`, `fetch_url`, `list_libraries`, and `find_version` are proxied to
the `docs` service. They require the `docs` container (skip with `--no-docs` in
install.sh, which drops them).

## Ports

| Port | Service | Job |
|------|---------|-----|
| 8091 | camofox | Browser REST API (container 9377) |
| 8092 | searxng | Metasearch |
| 8093 | mcp | MCP endpoint for clients |
| 8094 | docs | docs-mcp-server MCP docs tools + admin console |

Host ports bind to **localhost (127.0.0.1) by default** - nothing on the
network can reach them. Change the port numbers in `.env` (see
`.env.example`).

## Binding to the network (advanced)

By default all host ports bind to `127.0.0.1`, so only processes on the same
machine can reach them. If you want other machines to use the stack (e.g. an
MCP client on another computer), set `BIND_IP` in `.env` or pass
`--bind <ip>` to `install.sh`:

| BIND_IP value | What it binds to | When to use |
|---------------|------------------|-------------|
| `127.0.0.1` | localhost only | Default. Same-machine clients only. |
| `0.0.0.0` | all interfaces | LAN/internet reach (firewall permitting). Other machines use your machine's LAN IP. |
| `<LAN IP>` | one specific interface | Bind to e.g. `192.168.1.50` only. |

```bash
# via install.sh (one-shot, does not persist to .env)
./install.sh --bind 0.0.0.0

# via .env (persistent)
echo "BIND_IP=0.0.0.0" >> .env
docker compose up -d
```

Security note: binding to `0.0.0.0` exposes the browser REST API, the
metasearch, and the MCP endpoint to anything that can reach your machine.
Keep `127.0.0.1` unless you need network clients, and pair `0.0.0.0` with a
firewall rule that allows only trusted hosts.

## Configuration

Copy `.env.example` to `.env` to change any of these before first build:

| Var | Default | Meaning |
|-----|---------|---------|
| `CAMOFOX_HOST_PORT` | 8091 | host port for camofox |
| `SEARXNG_HOST_PORT` | 8092 | host port for searxng |
| `MCP_HOST_PORT` | 8093 | host port for mcp |
| `DOCS_HOST_PORT` | 8094 | host port for docs |
| `BIND_IP` | 127.0.0.1 | IP the host ports bind to (localhost only by default) |
| `CAMOUFOX_VERSION` | 135.0.1 | Camoufox browser build |
| `CAMOUFOX_RELEASE` | beta.24 | Camoufox release channel |
| `SEARXNG_VERSION` | latest | SearXNG image tag |
| `CAMOFOX_API_KEY` | (empty) | camofox cookie-import API key (optional) |
| `DOCS_EMBEDDING_MODEL` | (empty) | docs semantic-search embedding model (e.g. `ollama:nomic-embed-text`). Empty = pure full-text search |
| `OLLAMA_HOST` | (empty) | Ollama server URL when DOCS_EMBEDDING_MODEL is set |

## Indexing library docs

The `docs` service auto-indexes a default set in the **background** after a
fresh build (via the parallel one-shot `docs-seed` service): **JS** (MDN
reference), **TS** (TypeScript handbook), **NODE** (Node API), **PYTHON**
(Python 3 docs). Seeding runs concurrently with `docs` startup - deploy is not
blocked. Per-library idempotent: skips libraries already indexed to
**completed** status (failed/running/partial ones get re-scraped), backfills
the rest. Override the set with `DOCS_SEED_LIBRARIES` in `.env`
(comma-separated `name=url` pairs). Crawl tuning: `DOCS_SEED_MAX_PAGES`
(default 100 - lean, fast), `DOCS_SEED_MAX_DEPTH`, `DOCS_SEED_MAX_CONCURRENCY`,
`DOCS_SEED_SCRAPE_MODE` (default `fetch` - no headless browser; use
`playwright` only for JS-rendered SPA docs). Re-run/backfill manually with
`docker compose run --rm docs-seed`. The index persists in the `docs-index`
volume across restarts.

Index more libraries (or re-index) two ways:

1. **Web admin console** (easiest): open `http://localhost:8094`, click
   "Scrape New Library", give a name + docs URL. The console also shows
   indexing progress.
2. **CLI** (in a container with the same store): the sidecar's `scrape`,
   `search`, and `list` subcommands manage libraries.

```bash
# Example: index React's API reference (bundled CLI, no re-download)
docker exec mcpkitsune-docs node --enable-source-maps dist/index.js scrape react https://react.dev/reference/react
# Or backfill via the seed helper (respects DOCS_SEED_* tuning + idempotency):
#   docker compose run --rm docs-seed
```

The index persists in the `docs-index` volume across restarts/rebuilds.

## How it fits together

```text
+--------------------------------------------------------------+
| mcp_kitsune (docker compose)                                 |
|                                                              |
|  8091 -> camofox (browser, 9377)   jo-inc/camofox-browser    |
|            ^                                                 |
|            | http://camofox:9377 (Docker DNS)                |
|  8093 -> mcp (MCP endpoint, 8095)   kitsune                  |
|  8092 -> searxng (metasearch, 8080) searxng/searxng          |
|            | valkey (cache, internal)                        |
|  8094 -> docs (docs-mcp-server, 6280) arabold/docs-mcp-server|
|            | docs-index (volume, persists)                   |
+--------------------------------------------------------------+
```

The mcp service reaches the browser over the private Docker network, not the
internet. SearXNG caches in a private valkey. The docs sidecar keeps its index
in a private volume. Host ports bind to localhost (127.0.0.1) by default, so
nothing is exposed to the network - see "Binding to the network" above if you
need LAN clients.

## Why this split?

- **SearXNG only searches.** It cannot extract page content.
- **camofox/mcp only read.** A browser-engine search from a datacenter IP is
  CAPTCHA-blocked; SearXNG aggregates server-side instead.
- **docs-mcp-server only knows library docs.** It cannot search the general
  web or render JS pages - that's camofox.
- Normal web flow: `search_web` finds URLs -> `extract_web` reads each page and
  returns clean text. Docs flow: `search_docs` queries the indexed library
  index -> returns ranked markdown snippets (Context7-style).

## Troubleshooting

- **First build is slow** - normal, it downloads ~300 MB. Later builds use
  the image cache.
- **camofox keeps crashing / OOM** - Firefox needs shared memory. The compose
  sets `shm_size: 2g`. Bump to `4g` in `docker-compose.yml` if it still dies.
- **`camoufox.ok: false`** - mcp cannot reach the browser. Check
  `docker compose logs camofox`.
- **Port already in use** - change the host port in `.env`:
  `ss -tlnp | grep -E '809(1|2|3)'`.
- **Container name collision** - a previous stack is running. Stop it with
  `docker compose down`.
- **`search_docs` says "Library X not found"** - the docs index is empty or
  doesn't have that library yet. Index it (see "Indexing library docs").
- **`docs.ok: false` in /health** - the docs sidecar is down or not queryable.
  Check `docker compose logs docs`. The MCP endpoint only needs `docs` up, not
  `camofox`.

## Layout

```text
mcp_kitsune/
+-- docker-compose.yml    # 5-service stack
+-- install.sh            # one-shot deploy + verify
+-- .env.example          # port + version overrides
+-- .mcp.json             # Claude Code project config
+-- opencode.jsonc        # OpenCode project config
+-- AI.md                 # install guide for agents/CI
+-- skills/               # agent skill for the find -> read workflow
+-- docker/camofox/       # clone-on-the-fly Dockerfile + patches
+-- searxng/              # SearXNG settings
+-- mcp-combo/            # kitsune MCP server
```