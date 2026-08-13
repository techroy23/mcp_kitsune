#!/usr/bin/env bash
# =============================================================================
# docs-seed - one-shot indexer for the docs-mcp-server sidecar.
#
# Runs as an `init: true` container BEFORE the docs service starts. Scrapes a
# default set of libraries into the shared docs-index volume ONLY IF the store
# is empty (fresh build). Idempotent: if the volume already has libraries,
# this exits immediately without touching anything.
#
# Libraries come from DOCS_SEED_LIBRARIES (comma-separated "name=url" pairs)
# in the compose env. Defaults below match .env.example.
#
# Exit 0 on success, nonzero on scrape failure (which blocks `up` - see
# docker-compose.yml docs-seed). Safe to remove if you prefer manual indexing.
# =============================================================================
set -euo pipefail

# Default library -> official docs URL seed set. Overridable via env.
# Entry format: "name=url" or "name=url@mode" (mode: fetch|playwright|auto).
# Default mode is fetch (fast, no headless browser) - all four targets are
# server-rendered static doc sites; Playwright is only needed for JS-rendered
# SPAs (override per-entry or via DOCS_SEED_SCRAPE_MODE).
DEFAULT_LIBRARIES=(
  "js=https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference@fetch"
  "ts=https://www.typescriptlang.org/docs/handbook/intro.html@fetch"
  "node=https://nodejs.org/docs/latest/api/@fetch"
  "python=https://docs.python.org/3/@fetch"
)

# Scrape tuning: keep the auto-index lean - ~100 pages per library is plenty
# for everyday dev-docs lookup (a full crawl of MDN/Node/Python would take
# forever and bloat the volume). Override via DOCS_SEED_MAX_PAGES /
# DOCS_SEED_MAX_DEPTH / DOCS_SEED_MAX_CONCURRENCY / DOCS_SEED_SCRAPE_MODE.
MAX_PAGES="${DOCS_SEED_MAX_PAGES:-100}"
MAX_DEPTH="${DOCS_SEED_MAX_DEPTH:-4}"
MAX_CONCURRENCY="${DOCS_SEED_MAX_CONCURRENCY:-4}"

STORE_PATH="${DOCS_MCP_SERVER__APP__STORE_PATH:-/data}"
DEFAULT_SCRAPE_MODE="${DOCS_SEED_SCRAPE_MODE:-fetch}"
CLI=(node --enable-source-maps /app/dist/index.js)

log()  { echo "[docs-seed] $*"; }
fail() { echo "[docs-seed] ERROR: $*" >&2; exit 1; }

# --- Parse DOCS_SEED_LIBRARIES (comma-separated "name=url") or use defaults ---
parse_seed() {
  local raw="${DOCS_SEED_LIBRARIES:-}"
  if [ -z "$raw" ]; then
    SEEDS=("${DEFAULT_LIBRARIES[@]}")
    return
  fi
  IFS=',' read -r -a SEEDS <<< "$raw"
}

# --- Resolve which seed libraries are already indexed (idempotent) ----------
# The store can be partially seeded (e.g. interrupted). Skip ONLY libraries
# that have a COMPLETED version; re-scrape failed/running/empty ones.
# `list` returns JSON with each library's name + version status.
indexed_names() {
  "${CLI[@]}" list --store-path "$STORE_PATH" --output json 2>/dev/null \
    | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const a=JSON.parse(s.slice(s.indexOf('[')));const done=a.filter(l=>l.versions.some(v=>(v.status||'').toLowerCase()==='completed')).map(l=>l.name);console.log(done.join('\n'))}catch(e){}})" \
    | sort
}

main() {
  parse_seed

  log "store path: $STORE_PATH"
  local available
  available="$(indexed_names)"
  log "already indexed: ${available:-none}"

  local seeded=0 failed=0
  for entry in "${SEEDS[@]}"; do
    name="${entry%%=*}"
    rest="${entry#*=}"
    url="${rest%%@*}"
    mode="${rest#*@}"
    [ "$mode" = "$rest" ] && mode="$DEFAULT_SCRAPE_MODE"
    [ -n "$name" ] && [ -n "$url" ] || fail "bad seed entry: '$entry' (expected name=url[@mode])"

    # Per-library idempotency: skip it only if ALREADY indexed.
    if [ "$(echo "$available" | grep -qx "$name" && echo yes)" = "yes" ] && [ "${DOCS_SEED_FORCE:-0}" != "1" ]; then
      log "skip '$name' (already indexed)"
      continue
    fi

    log "scraping '$name' <- $url (mode=$mode)"
    if ! "${CLI[@]}" scrape "$name" "$url" --store-path "$STORE_PATH" \
        --max-pages "$MAX_PAGES" --max-depth "$MAX_DEPTH" --max-concurrency "$MAX_CONCURRENCY" \
        --scrape-mode "$mode" \
        >/tmp/seed_$name.log 2>&1; then
      log "scrape of '$name' FAILED (continuing with others) - tail of log:"
      tail -25 /tmp/seed_$name.log >&2
      failed=$((failed + 1))
      continue
    fi
    log "scraped '$name' OK"
    seeded=$((seeded + 1))
  done

  log "seed done: $seeded scraped, $failed failed"
  if [ "$failed" -gt 0 ]; then
    log "WARNING: $failed library/-ies failed - index is incomplete"
  fi
  "${CLI[@]}" list --store-path "$STORE_PATH" | head -40 || true
  exit 0
}

main "$@"
