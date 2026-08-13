# AI.md - Install + usage runbook for mcp_kitsune

This file is for an AI agent or CI pipeline that needs to get the mcp_kitsune
stack running and use it. README.md has the human-facing story. Follow this
file top to bottom. Each step is a decision: do this, check that, branch here.

## What you are setting up

A Docker Compose stack of four containers on one network:

| Container | Job | Host port |
|-----------|-----|-----------|
| camofox | anti-detection browser (REST API) | 8091 |
| searxng | metasearch engine (find URLs) | 8092 |
| mcp | MCP server (kitsune) - search + extract tools | 8093 |
| valkey | search cache (internal) | none |

The mcp container is the only one you talk to as a client. It exposes these
tools over MCP Streamable HTTP at `http://localhost:8093/mcp`:

- `search_web` - find URLs via SearXNG
- `extract_web` - render one page in Camoufox, return clean text
- `extract_web_batch` - render several pages in sequence
- `extract_structured` - pull fields using a JSON schema
- `browser_snapshot` - accessibility snapshot + screenshot

The normal flow is: `search_web` finds URLs, then `extract_web` (or batch)
reads each page. SearXNG only searches; camofox/mcp only read.

## Step 1 - preflight

Check before doing anything:

```bash
docker compose version   # must print a version (Compose plugin present)
docker info              # daemon running and reachable
df -h .                  # need ~2 GB free
```

If any of these fail, stop and report: Docker is not usable.

## Step 2 - install

One-shot installer (recommended):

```bash
./install.sh             # build + start + verify + prompts for skill install
./install.sh --no-skill  # same, but never prompt about the skill
```

What `install.sh` does, so you know what to expect:

1. Checks Docker, the Compose plugin, and that ports 8091/8092/8093 are free
   (skipped if this stack is already running - re-run is the upgrade path).
2. Creates `.env` from `.env.example` if missing. Never wipes an existing
   `.env`.
3. Builds images. First build downloads ~300 MB (Camoufox browser + yt-dlp),
   3-5 min. Retries once on the known-flaky Camoufox download.
4. Starts the stack and waits up to 120 s for `http://127.0.0.1:8093/health`
   to return `{"status":"ok",...}`.
5. Runs `verify_stack.py` if present.
6. Asks whether to install the agent skill (unless `--no-skill`).

Exit codes:

| Code | Meaning | What to do |
|------|---------|------------|
| 0 | stack up and healthy | proceed to Step 3 |
| 1 | preflight failure | read the error, fix Docker/ports, re-run |
| 2 | build or up failed | check `docker compose logs`, fix, re-run |
| 3 | health never became ok | check `docker compose logs mcp camofox`, fix, re-run |

If `install.sh` is not present (older checkout), use compose directly:

```bash
cp .env.example .env     # optional; defaults are fine
docker compose up -d --build
```

## Step 3 - verify

```bash
docker compose ps
# all four containers should show "Up (healthy)"

curl -s http://127.0.0.1:8093/health
```

The health JSON should contain `"status":"ok"` and
`"camoufox":{"ok":true,"browserConnected":true,...}`.

- `browserConnected: true` - the browser is up and the mcp server can reach
  it. Install is complete.
- `browserConnected: false` - the browser is idle (it shuts down after no
  sessions for a while). That is normal; the first extract will boot it. Do
  not treat it as a failure if `"status":"ok"` is present.
- `"status":"ok"` missing - something is wrong. See Step 5.

## Step 4 - register with an MCP client

The endpoint `http://<host>:8093/mcp` is the same for every client. Host
ports bind to **localhost (127.0.0.1) by default**, so use `localhost` when
the client is on the same machine as the stack. Only machines that share the
host can reach it otherwise. The three configs below are the supported
clients. `.mcp.json` and `opencode.jsonc` are already committed in the repo
and auto-detected when the project is opened, so you may not need to register
at all.

If the client runs on a *different* machine, the stack must be configured to
bind to the network first - see "Binding to the network" below. Until you set
that, a remote client cannot connect.

### Hermes

```bash
hermes mcp add kitsune --transport streamable-http --url http://localhost:8093/mcp
hermes mcp test kitsune    # verify the connection
```

### Claude Code

```bash
claude mcp add --transport http kitsune http://localhost:8093/mcp
claude mcp list            # should show kitsune with a Connected status
```

Project-scoped alternative: the committed `.mcp.json` is picked up when the
project is opened. It must keep `"type": "http"` - Claude Code reads an entry
with only `url` as a stdio server and skips it.

### OpenCode

```bash
# global: add to ~/.config/opencode/opencode.jsonc under "mcp":
#   "kitsune": { "type": "remote", "url": "http://localhost:8093/mcp", "enabled": true }
opencode mcp    # verify kitsune is listed
```

Project-scoped alternative: the committed `opencode.jsonc` is auto-detected.

## Step 5 - use the stack (the part that matters)

Once registered, call the tools to confirm the full loop works:

```text
1. search_web(query="test", max_results=3)
   -> expect a list of results with title + url. If empty or an error, the
      SearXNG -> mcp wiring is broken; check `docker compose logs mcp searxng`.

2. extract_web(url=<first result url>, wait_ms=3000, max_chars=5000)
   -> expect clean page text. If this fails or times out, the camofox ->
      mcp wiring is broken; check `docker compose logs mcp camofox`.

3. If both work, the stack is usable. Use search_web + extract_web /
   extract_web_batch for the actual research task. Prefer extract_web_batch
   over looping extract_web for many URLs (reuses the browser session).
```

Do not use camofox (port 8091) directly unless you need raw browser control
(navigate, evaluate JS). For normal research, the MCP tools are the intended
path.

## Step 6 - stop / start

```bash
docker compose down   # stop everything
docker compose up -d  # start again (instant, images cached)
```

`docker compose down` does not delete images or the `.env`.

## Configuration

Copy `.env.example` to `.env` to change any of these before first build:

| Var | Default | Meaning |
|-----|---------|---------|
| `CAMOFOX_HOST_PORT` | 8091 | host port for camofox |
| `SEARXNG_HOST_PORT` | 8092 | host port for searxng |
| `MCP_HOST_PORT` | 8093 | host port for mcp |
| `BIND_IP` | 127.0.0.1 | IP the host ports bind to (localhost only by default) |
| `CAMOUFOX_VERSION` | 135.0.1 | Camoufox browser build |
| `CAMOUFOX_RELEASE` | beta.24 | Camoufox release channel |
| `SEARXNG_VERSION` | latest | SearXNG image tag |
| `CAMOFOX_API_KEY` | (empty) | camofox cookie-import API key (optional) |

## Binding to the network (only if a remote client needs it)

Host ports bind to `127.0.0.1` by default, so only processes on the same
machine can reach the stack. If an MCP client runs on another machine, it
cannot connect until you change the bind. Choose deliberately:

| BIND_IP value | What it binds to | When to use |
|---------------|------------------|-------------|
| `127.0.0.1` | localhost only | Default. Same-machine clients only. |
| `0.0.0.0` | all interfaces | LAN/internet reach (firewall permitting). Clients use the host's LAN IP. |
| `<LAN IP>` | one specific interface | Bind to e.g. `192.168.1.50` only. |

Apply it one of two ways:

```bash
# one-shot via the installer (does not persist to .env)
./install.sh --bind 0.0.0.0 --no-skill

# persistent via .env
echo "BIND_IP=0.0.0.0" >> .env
docker compose up -d
```

Security note: `0.0.0.0` exposes the browser REST API, the metasearch, and
the MCP endpoint to anything that can reach the host. Prefer a specific LAN
IP, and pair any non-localhost bind with a firewall rule that allows only
trusted hosts. Never bind `0.0.0.0` on a host that is directly on the public
internet.

## Troubleshooting (agent decision tree)

- **Build hangs on first `up --build`** - normal, it downloads ~300 MB. Wait,
  do not kill it. If it fails, re-run once (known flaky Camoufox download).
- **`camoufox.ok: false` in /health** - mcp cannot reach camofox. Run
  `docker compose logs camofox`. Confirm `shm_size: 2g` is still in the
  compose (removing it causes OOM crashes).
- **camofox keeps restarting** - check `docker compose ps` for a high
  RestartCount, then `docker compose logs camofox | tail`. Firefox needs
  shared memory; bump `shm_size` to `4g` in docker-compose.yml if OOM.
- **Port already in use** - run `ss -tlnp | grep -E '809(1|2|3)'`, find the
  process, then change the host port in `.env` (never the container port).
- **Container name collision** - a previous stack is running. Run
  `docker compose down` first, then `up` again.

## Installing the agent skill (ask the user first)

The repo ships one skill file, `skills/search-extraction-combo.md`, that
teaches the find -> read workflow. Each agent runtime stores skills in a
different directory, so before installing it you MUST ask the user which
agent they want it installed for. Do not guess - the wrong path fails silently
or lands in another project's skill store.

Per-platform install paths:

| Platform | Destination |
|----------|-------------|
| Hermes | `~/.hermes/skills/research/search-extraction-combo/SKILL.md` |
| Claude Code | `.claude/skills/search-extraction-combo/SKILL.md` |
| OpenCode | `.opencode/skills/search-extraction-combo/SKILL.md` |

After copying, verify the frontmatter (`name` + `description`) parses and the
file is byte-identical to `skills/search-extraction-combo.md`. Keep the
frontmatter untouched - the skill only triggers if its `description` fits the
agent's trigger index.

If the user names an unlisted platform, ask for the correct skill directory
or whether to keep the file in-repo only.
