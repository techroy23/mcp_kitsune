# mcp_kitsune - self-contained search + extract for AI agents

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

One command starts all four containers (browser, search, cache, MCP gateway).
Everything stays on your machine.

## Quick start

Requires Docker with the Compose plugin. About 2 GB free disk.

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
# all four containers should show "Up (healthy)"

curl -s http://localhost:8093/health
# {"status":"ok","camoufox":{"ok":true,"browserConnected":true,...},...}
```

`camoufox.ok: true` means the MCP server can reach the browser. Stop with
`docker compose down`, start again with `docker compose up -d`.

## Using it from an MCP client

The stack exposes one endpoint: `http://<host>:8093/mcp` (Streamable HTTP).
The registration command differs per client; the endpoint is the same. Use
`localhost` when the client runs on the same machine, your LAN IP otherwise.

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

## Ports

| Port | Service | Job |
|------|---------|-----|
| 8091 | camofox | Browser REST API (container 9377) |
| 8092 | searxng | Metasearch |
| 8093 | mcp | MCP endpoint for clients |

All host ports bind to localhost by default. Change them in `.env` (see
`.env.example`).

## Configuration

Copy `.env.example` to `.env` to change any of these before first build:

| Var | Default | Meaning |
|-----|---------|---------|
| `CAMOFOX_HOST_PORT` | 8091 | host port for camofox |
| `SEARXNG_HOST_PORT` | 8092 | host port for searxng |
| `MCP_HOST_PORT` | 8093 | host port for mcp |
| `CAMOUFOX_VERSION` | 135.0.1 | Camoufox browser build |
| `CAMOUFOX_RELEASE` | beta.24 | Camoufox release channel |
| `SEARXNG_VERSION` | latest | SearXNG image tag |
| `CAMOFOX_API_KEY` | (empty) | camofox cookie-import API key (optional) |

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
+--------------------------------------------------------------+
```

The mcp service reaches the browser over the private Docker network, not the
internet. SearXNG caches in a private valkey. Host ports bind to localhost by
default, so nothing is exposed publicly.

## Why this split?

- **SearXNG only searches.** It cannot extract page content.
- **camofox/mcp only read.** A browser-engine search from a datacenter IP is
  CAPTCHA-blocked; SearXNG aggregates server-side instead.
- Normal flow: `search_web` finds URLs -> `extract_web` reads each page and
  returns clean text.

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

## Layout

```text
mcp_kitsune/
+-- docker-compose.yml    # 4-service stack
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

## License

MIT
