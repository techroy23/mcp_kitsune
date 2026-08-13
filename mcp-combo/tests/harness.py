"""
Live full-functionality harness for kitsune.

Starts the real MCP server (uvicorn) in a thread, then over HTTP drives the
full MCP protocol:
  1. GET /health   -> ok + camoufox upstream status + tool list
  2. MCP initialize -> capture mcp-session-id (required on later requests)
  3. tools/list    -> all 5 tools present
  4. ping
  5. tools/call extract_web         -> clean text from a real page
  6. tools/call extract_web_batch   -> 2 URLs extracted
  7. error handling: bad URL, missing param, unknown tool -> isError true

SSE parsing: MCP over streamable-HTTP returns text/event-stream where each
event is `event: message` + `data: {...}`. We parse `data:` lines.

Run:  .venv/bin/python -m tests.harness
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import httpx
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MCP_PORT", "8096")
# Host-side tests resolve SearXNG through the published host port (the in-stack
# Docker-DNS name `searxng` only resolves inside the compose network).
os.environ.setdefault("SEARXNG_BASE_URL", "http://127.0.0.1:8092")

from kitsune import server as srv  # noqa: E402

PORT = int(os.environ["MCP_PORT"])
BASE = f"http://127.0.0.1:{PORT}"
PASS = 0
FAIL = 0

SSE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def parse_sse_data(text: str) -> list[dict]:
    """Return the JSON objects carried in an SSE text body."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                out.append(json.loads(payload))
    return out


def latest_message(text: str) -> dict | None:
    """Return the last 'event: message' payload (the final result)."""
    events = parse_sse_data(text)
    return events[-1] if events else None


def mcp_post(client: httpx.Client, body: dict, session_id: str | None, timeout: float = 180) -> tuple[int, str, str | None]:
    """POST a JSON-RPC body to /mcp. Returns (status, raw body, new session id)."""
    headers = dict(SSE_HEADERS)
    if session_id:
        headers["mcp-session-id"] = session_id
    resp = client.post(f"{BASE}/mcp", json=body, headers=headers, timeout=timeout)
    return resp.status_code, resp.text, resp.headers.get("mcp-session-id")


def main() -> None:
    # --- start server in background thread ---
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
    t = threading.Thread(target=uvicorn.Server(config).run, daemon=True, name="uvicorn")
    t.start()
    time.sleep(0.5)

    with httpx.Client(timeout=60) as client:
        # wait for readiness
        ready = False
        for _ in range(60):
            try:
                if client.get(f"{BASE}/health", timeout=3).status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not ready:
            print("FATAL: server did not become ready")
            sys.exit(1)

        print("== HTTP endpoints ==")
        code, health = client.get(f"{BASE}/health").status_code, client.get(f"{BASE}/health").json()
        check("GET /health 200", code == 200, f"got {code}")
        check("health.camoufox.ok is True", health.get("camoufox", {}).get("ok") is True, json.dumps(health)[:200])
        check(
            "health lists 5 mcp tools",
            len(health.get("mcp_tools", [])) == 5
            and health.get("mcp_tools") == ["search_web", "extract_web", "extract_web_batch", "extract_structured", "browser_snapshot"],
            str(health.get("mcp_tools")),
        )

        print("== MCP initialize ==")
        code, text, session_id = mcp_post(client, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "harness", "version": "0.0.1"}},
        }, None, timeout=30)
        check("initialize HTTP 200", code == 200, f"got {code}, body={text[:200]}")
        check("initialize returns mcp-session-id", bool(session_id), f"sid={session_id}")
        msg = latest_message(text)
        check("initialize serverInfo.name = kitsune",
              msg and msg.get("result", {}).get("serverInfo", {}).get("name") == "kitsune",
              (text[:300] if not msg else json.dumps(msg)[:300]))
        # mark initialized (protocol requirement before tools/list)
        code, _text, _sid = mcp_post(client, {
            "jsonrpc": "2.0", "method": "notifications/initialized"}, session_id, timeout=30)
        check("notifications/initialized accepted", code in (200, 202), f"got {code}")

        print("== tools/list ==")
        code, text, _sid = mcp_post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id, timeout=30)
        msg = latest_message(text)
        tools = msg.get("result", {}).get("tools", []) if msg else []
        names = [t["name"] for t in tools]
        for expected in ["search_web", "extract_web", "extract_web_batch", "extract_structured", "browser_snapshot"]:
            check(f"tools/list has {expected}", expected in names, str(names))

        print("== ping ==")
        code, text, _sid = mcp_post(client, {"jsonrpc": "2.0", "id": 3, "method": "ping"}, session_id, timeout=30)
        msg = latest_message(text)
        check("ping returns result", code == 200 and msg and "result" in msg, (text[:200] if not msg else json.dumps(msg)[:200]))

        def call_tool(name: str, args: dict) -> tuple[int, dict | None]:
            code, text, _sid = mcp_post(client, {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": name, "arguments": args}}, session_id, timeout=180)
            return code, latest_message(text)

        def tool_content(ev: dict | None) -> str:
            if not ev or "result" not in ev:
                return ""
            items = ev["result"].get("content", [])
            return "".join(c.get("text", "") for c in items if c.get("type") == "text")

        print("== extract_web (real page) ==")
        code, ev = call_tool("extract_web", {"url": "https://example.com", "wait_ms": 2500})
        check("extract_web call HTTP 200", code == 200, f"got {code}")
        blob = tool_content(ev)
        check("extract_web returns readable text", "Example Domain" in blob or "This domain is for use" in blob, blob[:250])
        check("extract_web marks extracted_with=camoufox-mcp", '"camoufox-mcp"' in blob, blob[:250])

        print("== extract_web_batch (2 URLs) ==")
        code, ev = call_tool("extract_web_batch", {"urls": ["https://example.com", "https://example.org"], "wait_ms": 1500})
        check("batch call HTTP 200", code == 200, f"got {code}")
        blob = tool_content(ev)
        check("batch returns 2 URL entries", blob.count('"url"') >= 2, blob[:400])
        check("batch includes example.org", "example.org" in blob, blob[:400])
        check("batch has no errors", '"error"' not in blob, blob[:400])

        print("== search_web (real query) ==")
        code, ev = call_tool("search_web", {"query": "example domain", "max_results": 3})
        check("search_web call HTTP 200", code == 200, f"got {code}")
        blob = tool_content(ev)
        check("search_web returns results", '"numResults"' in blob and '"results"' in blob, blob[:400])
        check("search_web results non-empty", '"example.com"' in blob or '"example.org"' in blob or blob.count('"url"') > 0, blob[:400])

        print("== error handling ==")
        code, ev = call_tool("extract_web", {"url": "not-a-url"})
        check("bad URL -> isError", ev and ev.get("result", {}).get("isError") is True, json.dumps(ev)[:300])
        code, ev = call_tool("extract_web", {})
        check("missing param -> isError", ev and ev.get("result", {}).get("isError") is True, json.dumps(ev)[:300])
        code, ev = call_tool("does_not_exist", {})
        check("unknown tool -> isError", ev and ev.get("result", {}).get("isError") is True, json.dumps(ev)[:300])

    print(f"\n==== RESULT: {PASS} passed, {FAIL} failed ====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()