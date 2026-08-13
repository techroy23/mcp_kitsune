"""End-to-end test: call the kitsune MCP server's search_web tool over streamable HTTP.

Inspects the live server at http://localhost:8093/mcp (initialize -> tools/list ->
tools/call search_web) without needing a Hermes restart. Verifies the new
search_web tool is registered and actually returns results from SearXNG.

NOTE: The SDK v2 streamable-HTTP transport answers with SSE framing by default
(content-type text/event-stream: lines 'event: message' + 'data: <json>'), and
subsequent requests must echo the 'mcp-session-id' response header.
"""
import asyncio
import json

import httpx

MCP_ENDPOINT = "http://localhost:8093/mcp"


def parse_sse(resp: httpx.Response) -> dict:
    """Extract the first JSON payload from an SSE-framed MCP response."""
    # body looks like: event: message\r\ndata: {json}\r\n\r\n
    data_line = None
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            data_line = line[len("data: "):]
            break
    if data_line is None:
        # fall back to raw JSON (non-streaming clients / batch responses)
        return resp.json()
    return json.loads(data_line)


async def main() -> None:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # 1) initialize
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "kitsune-verify", "version": "0.0.1"},
            },
        }
        r = await client.post(MCP_ENDPOINT, json=init)
        r.raise_for_status()
        init_resp = parse_sse(r)
        session_id = r.headers.get("mcp-session-id")
        print("=== initialize ===")
        print("serverInfo:", init_resp.get("result", {}).get("serverInfo"))
        print("session-id header:", session_id)

        headers = {}
        if session_id:
            headers["mcp-session-id"] = session_id

        # 2) tools/list
        r = await client.post(
            MCP_ENDPOINT,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        tools = parse_sse(r)["result"]["tools"]
        names = [t["name"] for t in tools]
        print("\n=== tools/list ===")
        print("tools:", names)
        assert "search_web" in names, "search_web missing from tools/list!"

        # 3) tools/call search_web
        r = await client.post(
            MCP_ENDPOINT,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_web",
                    "arguments": {"query": "install terraform ubuntu 24.04", "max_results": 5},
                },
            },
            headers=headers,
        )
        call = parse_sse(r)
        print("\n=== tools/call search_web ===")
        # text content is JSON inside content[0].text
        text = None
        if "result" in call and "content" in call["result"]:
            for c in call["result"]["content"]:
                if c.get("type") == "text":
                    text = c["text"]
        if text:
            parsed = json.loads(text)
            print("query:", parsed.get("query"))
            print("numResults:", parsed.get("numResults"))
            for res in parsed.get("results", [])[:3]:
                print(f"  - {res.get('title')}\n    {res.get('url')}")
        else:
            print("RAY CALL RESULT:", json.dumps(call, indent=2)[:2000])

        if "error" in call:
            print("\nMCP ERROR:", call["error"])


if __name__ == "__main__":
    asyncio.run(main())