#!/usr/bin/env bash
# =============================================================================
# install.sh - one-shot deploy of the mcp_kitsune stack (AI-friendly)
#
# Builds and starts the full stack (camofox stealth browser + searxng search +
# valkey + the kitsune MCP server) and verifies it is healthy before exiting.
# Safe to re-run (idempotent): never wipes data, preserves an existing .env,
# and rebuilds only what changed. Designed to be run by an AI agent OR a human.
#
# Exit codes:
#   0  stack is up and healthy (mcp /health ok + camofox reachable)
#   1  preflight failure (missing docker, missing compose, port in use)
#   2  build/up failure
#   3  health verification failure (stack up but not healthy)
#   4  install of the agent skill file was declined or skipped (non-fatal)
#
# Usage:
#   ./install.sh            # deploy + verify + optionally install skill
#   ./install.sh --no-skill # skip the skill-file question (pure deploy)
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# --- helpers ----------------------------------------------------------------
log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m   %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

# --- flags ------------------------------------------------------------------
INSTALL_SKILL=1
for arg in "$@"; do
  case "$arg" in
    --no-skill) INSTALL_SKILL=0 ;;
    -h|--help)
      grep -E '^# Usage' -A4 "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) err "unknown argument: $arg (see --help)"; exit 1 ;;
  esac
done

# --- 1. preflight -----------------------------------------------------------
log "preflight"
if ! command -v docker >/dev/null 2>&1; then
  err "docker not found. Install Docker (https://docs.docker.com/engine/install/) then re-run."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  err "docker compose plugin not available (need 'docker compose', not the standalone docker-compose)."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  err "docker daemon not running or not accessible. Start Docker then re-run."
  exit 1
fi
ok "docker + compose present"

# Ports the stack binds on the host (override via .env).
# env_val VAR   -> value of VAR in .env (else .env.example), stripped of inline
#                  comments and whitespace (e.g. '8093        # comment' -> 8093)
env_val() {
  local v
  v="$(grep -E "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  [ -n "$v" ] || v="$(grep -E "^${1}=" .env.example | head -1 | cut -d= -f2- || true)"
  printf '%s' "$v" | sed -E 's/[[:space:]]*#.*$//' | tr -d '[:space:]'
}

# If the stack containers are already running, its own ports are in use - that
# is fine (re-run / upgrade path). Only fail when a NON-stack process holds a port.
STACK_RUNNING=0
if docker compose ps --status running 2>/dev/null | grep -q "mcpkitsune-"; then
  STACK_RUNNING=1
  log "existing mcp_kitsune stack detected - upgrade/re-run mode (ports already held by it)"
fi

for port_var in CAMOFOX_HOST_PORT SEARXNG_HOST_PORT MCP_HOST_PORT; do
  port="$(env_val "$port_var")"
  if [ -n "$port" ] && ss -tln 2>/dev/null | grep -qE "[:.]${port} "; then
    if [ "$STACK_RUNNING" -eq 1 ]; then
      ok "port $port held by the running stack (expected)"
    else
      err "port $port ($port_var) already in use by another process. Change it in .env or free the port."
      exit 1
    fi
  fi
done
ok "required ports free"

# --- 2. environment ---------------------------------------------------------
if [ ! -f .env ]; then
  log "no .env found - copying .env.example (defaults are fine)"
  cp .env.example .env
  ok ".env created"
else
  log ".env exists - reusing (no secrets wiped)"
fi

# --- 3. build + up ----------------------------------------------------------
log "building images (first build downloads the Camoufox browser, ~300 MB, 3-5 min)"
if ! docker compose build; then
  warn "initial build failed. Retrying once (known flaky Camoufox release download)..."
  sleep 3
  docker compose build || { err "build failed twice"; exit 2; }
fi
ok "images built"

log "starting stack"
docker compose up -d || { err "docker compose up failed"; exit 2; }
ok "containers started"

# --- 4. wait for health -----------------------------------------------------
log "waiting for mcp /health (up to 120 s)"
MCP_PORT="$(env_val MCP_HOST_PORT)"
[ -n "$MCP_PORT" ] || MCP_PORT="8093"
HEALTH_URL="http://127.0.0.1:${MCP_PORT}/health"
for i in $(seq 1 24); do
  if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -qE '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok "mcp /health ok after ${i}x5s"
    break
  fi
  [ "$i" -eq 24 ] && { err "mcp /health never became ok. Check 'docker compose logs mcp'."; exit 3; }
  sleep 5
done

# Verify camofox is actually reachable from mcp (the depends_on health gate
# waits for camofox, but confirm the browser answers too).
if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -qE '"(browserConnected|browserRunning)"[[:space:]]*:[[:space:]]*true'; then
  ok "camofox browser connected"
else
  warn "mcp /health ok but browser not connected yet - first extract will boot it (normal after idle shutdown)"
fi

# --- 5. verify_stack --------------------------------------------------------
if [ -f verify_stack.py ]; then
  log "running verify_stack.py"
  if python3 verify_stack.py >/dev/null 2>&1; then
    ok "verify_stack.py passed"
  else
    warn "verify_stack.py reported issues (stack is up; check output manually)"
  fi
fi

# --- 6. skill install (optional) -------------------------------------------
if [ "$INSTALL_SKILL" -eq 1 ] && [ -f skills/search-extraction-combo.md ]; then
  log "found agent skill: skills/search-extraction-combo.md"
  echo "This repo ships a skill that teaches agents the find -> read workflow."
  echo "It must be installed into YOUR agent's skill directory (paths differ per agent)."
  read -r -p "Install it now? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES)
      DEST="${HERMES_SKILL_DIR:-$HOME/.hermes/skills/research/search-extraction-combo}"
      mkdir -p "$DEST"
      cp skills/search-extraction-combo.md "$DEST/SKILL.md"
      ok "skill installed to $DEST/SKILL.md (Hermes default)."
      ok "For Claude Code / OpenCode see AI.md 'Install the skill file'."
      ;;
    *)
      warn "skill install skipped (see AI.md for per-agent paths)"
      ;;
  esac
fi

# --- 7. summary -------------------------------------------------------------
echo
log "DONE - stack is up"
echo "  MCP endpoint : http://localhost:${MCP_PORT}/mcp"
echo "  Health check : $HEALTH_URL"
echo "  Tools        : search_web, extract_web, extract_web_batch, extract_structured, browser_snapshot"
echo "  Register     : hermes mcp add kitsune --transport streamable-http --url http://localhost:${MCP_PORT}/mcp"
echo "                 (Claude Code / OpenCode: see AI.md)"
echo
echo "  Verify manually:"
echo "    docker compose ps"
echo "    curl $HEALTH_URL"
exit 0
