"""
kitsune - MCP web research gateway (SearXNG search + Camoufox extract + docs search).

Turns the self-hosted stack into first-class research capabilities for agents:
- search_web through SearXNG (find URLs, no CAPTCHA block at datacenter IPs)
- extract_web / extract_structured / browser_snapshot through the Camoufox
  anti-detection browser (read JS-heavy / bot-protected pages)
- search_docs / fetch_url proxied to the docs-mcp-server sidecar (Context7-style
  library documentation search)

Why this exists: SearXNG is search-only (no page-fetch/extract). Exa/Firecrawl
are cloud. Camoufox is self-hosted, free, stealth, and already deployed on the
homelab - so it is the natural default extract provider for scraping and
research tasks.

Transport: MCP streamable-HTTP (SDK v2 `MCPServer`), mounted at root so the
MCP endpoint lands on /mcp. Serves /health and /mcp on the same port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

logger = logging.getLogger("mcp_kitsune")

# ---------------------------------------------------------------------------
# Configuration (env-overridable so the same code runs on LAN / VPS)
# ---------------------------------------------------------------------------
CAMOUFOX_BASE_URL = os.environ.get("CAMOUFOX_BASE_URL", "http://localhost:8091")
CAMOUFOX_USER_ID = os.environ.get("CAMOUFOX_USER_ID", "hermes-agent")
CAMOUFOX_SESSION_KEY = os.environ.get("CAMOUFOX_SESSION_KEY", "mcp-kitsune")
# SearXNG search backend (in-stack DNS when running inside the same compose).
SEARXNG_BASE_URL = os.environ.get("SEARXNG_BASE_URL", "http://searxng:8080")
SEARXNG_TIMEOUT = float(os.environ.get("SEARXNG_TIMEOUT", "15"))
# docs-mcp-server sidecar: self-hosted library documentation search
# (Context7 replacement). In-stack DNS name when running in the same compose.
DOCS_MCP_BASE_URL = os.environ.get("DOCS_MCP_BASE_URL", "http://docs:6280")
DOCS_MCP_TIMEOUT = float(os.environ.get("DOCS_MCP_TIMEOUT", "30"))
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8095"))
HTTP_TIMEOUT = float(os.environ.get("CAMOUFOX_HTTP_TIMEOUT", "90"))
# Transport security: allow MCP clients from other hosts (Docker, LAN) via env.
# Format: comma-separated host patterns and origins (e.g. "*", "0.0.0.0:*", "http://*").
MCP_ALLOWED_HOSTS = os.environ.get("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")
MCP_ALLOWED_ORIGINS = os.environ.get("MCP_ALLOWED_ORIGINS", "")
# Disable DNS-rebinding protection entirely (trusted internal network / container).
# When True the allowed_hosts/origins above are the only accepted Host/Origin.
MCP_ENABLE_DNS_REBINDING_PROTECTION = os.environ.get("MCP_ENABLE_DNS_REBINDING_PROTECTION", "true").lower() in ("1", "true", "yes", "on")

# Readability-inspired JS: strip chrome (nav/header/footer/script/style), get
# the main text, and return a clean markdown-ish rendering with links.
# NOTE: must be an IIFE (self-invoking) - the Camoufox evaluate endpoint
# serializes the RESULT of the expression; a bare object literal or bare
# function object returns null. `(() => {...})()` executes and returns its value.
EXTRACT_TEXT_JS = r"""
(() => {
  const removeSelectors = [
    'script', 'style', 'noscript', 'svg', 'canvas', 'iframe',
    'nav', 'header', 'footer', 'aside',
    '[role="navigation"]', '[role="banner"]', '[role="complementary"]',
    '.nav', '.navbar', '.menu', '.sidebar', '.footer', '.header',
    '.cookie-banner', '.cookie-consent', '.advertisement', '.ads', '.ad',
    '.popup', '.modal', '.overlay', '#cookie-banner', '#cookieConsent',
    '#ads', '#ad'
  ];
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll(removeSelectors.join(',')).forEach(el => el.remove());

  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
  const parts = [];
  let lastWasBlock = false;
  const pushBlock = (text) => {
    const t = text.trim().replace(/\s+/g, ' ');
    if (t) {
      if (lastWasBlock) parts.push('\n');
      parts.push(t);
      lastWasBlock = true;
    }
  };
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent) continue;
    const tag = parent.tagName.toLowerCase();
    if (['script','style','noscript','svg','canvas','iframe'].includes(tag)) continue;
    const text = node.nodeValue || '';
    if (!text.trim()) continue;
    if (['h1','h2','h3','h4','h5','h6','p','li','td','th','blockquote','pre','br'].includes(tag)) {
      pushBlock(text);
      if (tag === 'pre') { parts.push('\n'); lastWasBlock = false; }
    } else {
      parts.push(text.trim() + ' ');
      lastWasBlock = false;
    }
  }
  const text = parts.join('').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();

  const links = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const href = a.href;
    const label = (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200);
    if (href && !href.startsWith('javascript:') && !seen.has(href)) {
      seen.add(href);
      links.push({href, label});
    }
  });

  return JSON.stringify({
    title: document.title,
    url: location.href,
    text: text.slice(0, 600000),
    links: links.slice(0, 2000),
    charCount: text.length
  });
})()
"""


class CamoufoxClient:
    """Thin async HTTP client for the Camoufox REST API (per openapi.json v1.13)."""

    def __init__(self, base_url: str, user_id: str, session_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.session_key = session_key
        self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Camoufox API {method} {path} -> HTTP {resp.status_code}: {resp.text[:400]}")
        if resp.status_code == 204:
            return {}
        return resp.json()

    async def start_browser(self) -> dict[str, Any]:
        return await self._request("POST", "/start")

    async def create_tab(self, url: str | None = None) -> str:
        payload: dict[str, Any] = {"userId": self.user_id, "sessionKey": self.session_key}
        if url:
            payload["url"] = url
        data = await self._request("POST", "/tabs", json=payload)
        return str(data["tabId"])

    async def navigate(self, tab_id: str, url: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/navigate", json={"userId": self.user_id, "url": url}
        )

    async def wait(self, tab_id: str, timeout_ms: int) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/wait", json={"userId": self.user_id, "timeout": timeout_ms}
        )

    async def snapshot(self, tab_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/tabs/{tab_id}/snapshot", params={"userId": self.user_id}
        )

    async def evaluate(self, tab_id: str, expression: str) -> Any:
        data = await self._request(
            "POST", f"/tabs/{tab_id}/evaluate",
            json={"userId": self.user_id, "expression": expression},
        )
        return data.get("result")

    async def extract(self, tab_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        data = await self._request(
            "POST", f"/tabs/{tab_id}/extract",
            json={"userId": self.user_id, "schema": schema},
        )
        return data

    async def close_tab(self, tab_id: str) -> None:
        try:
            # DELETE /tabs/{id} requires userId (query or body); send as body.
            await self._request("DELETE", f"/tabs/{tab_id}", json={"userId": self.user_id})
        except Exception as exc:  # cleanup best-effort
            logger.warning("close_tab(%s) failed: %s", tab_id, exc)

    async def close_session(self) -> None:
        try:
            await self._request("DELETE", f"/sessions/{self.user_id}")
        except Exception as exc:
            logger.warning("close_session failed: %s", exc)

    async def get_page_text(self, url: str, wait_ms: int = 3000) -> dict[str, Any]:
        """Open a tab, navigate, wait, and extract clean text via JS evaluation."""
        await self.start_browser()
        tab_id = await self.create_tab()
        try:
            await self.navigate(tab_id, url)
            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000.0)
            result = await self.evaluate(tab_id, EXTRACT_TEXT_JS)
            if isinstance(result, str):
                return json.loads(result)
            return {"error": "evaluate did not return a JSON string", "raw": result}
        finally:
            await self.close_tab(tab_id)


class DocsMcpClient:
    """Thin async HTTP client for the docs-mcp-server sidecar (MCP streamable HTTP).

    Proxies tools/call requests to the Node sidecar so kitsune can expose
    search_docs/fetch_url under one coherent MCP surface. The sidecar does the
    heavy lifting (indexing, FTS/vector search); this class just forwards.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": f"kitsune-{time.time():.3f}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/mcp",
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"docs-mcp-server {tool_name} -> HTTP {resp.status_code}: {resp.text[:300]}"
                )
            # docs-mcp-server responds with SSE framing even to POST (event:
            # message\\ndata: {json}). Strip the event/data envelope before parse.
            text = resp.text
            if text.lstrip().startswith("event:"):
                data_line = next(
                    (line[5:].strip() for line in text.splitlines() if line.startswith("data:")),
                    None,
                )
                if data_line is None:
                    raise RuntimeError(f"docs-mcp-server {tool_name}: SSE without data line")
                data = json.loads(data_line)
            else:
                data = resp.json()
        # Extract the text content array from the MCP result envelope.
        result = data.get("result", {}) if isinstance(data, dict) else {}
        return {
            "content": result.get("content", []),
            "isError": result.get("isError", False),
        }


# ---------------------------------------------------------------------------
# MCP server (SDK v2)
# ---------------------------------------------------------------------------
mcp = MCPServer("kitsune")
_client: CamoufoxClient | None = None


def get_client() -> CamoufoxClient:
    global _client
    if _client is None:
        _client = CamoufoxClient(CAMOUFOX_BASE_URL, CAMOUFOX_USER_ID, CAMOUFOX_SESSION_KEY)
    return _client


def _validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid URL (must be http/https with host): {url!r}")
    # reject malformed ports (e.g. "https://host:abc") that urlparse silently okays
    try:
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            raise ValueError(f"URL port out of range: {url!r}")
    except ValueError:
        raise ValueError(f"URL has an invalid port: {url!r}")
    return url


@mcp.tool()
async def extract_web(url: str, wait_ms: int = 3000, max_chars: int = 20000, max_links: int = 50) -> dict[str, Any]:
    """Fetch a web page in a stealth browser and return its clean readable content.

    Best for: pages that block plain HTTP fetchers, JS-heavy pages, or when you
    need clean article text + link list. Returns title, url, markdown text, and
    page links.

    Args:
        url: The http(s) URL to extract.
        wait_ms: How long to wait for JS/network settle before reading (default 3000).
        max_chars: Cap on returned text length (default 20000; raise only when you
            genuinely need the whole page - large pages can blow context).
        max_links: Cap on returned link count (default 50). Prevents nav/footer-heavy
            pages (GitHub, portals, docs) from ballooning the response.
    """
    _validate_url(url)
    try:
        data = await get_client().get_page_text(url, wait_ms=wait_ms)
    except Exception as exc:
        return {"error": f"extract failed: {exc}", "url": url}
    if "error" in data:
        return data
    text = (data.get("text") or "")[:max_chars]
    links = (data.get("links") or [])[:max_links]
    return {
        "url": data.get("url", url),
        "title": data.get("title", ""),
        "content": text,
        "charCount": len(text),
        "links": links,
        "extracted_with": "camoufox-mcp",
    }


@mcp.tool()
async def extract_web_batch(urls: list[str], wait_ms: int = 2000, max_chars: int = 20000, max_links: int = 50) -> list[dict[str, Any]]:
    """Extract multiple pages in sequence (reuses one browser session/tab each).

    Best for: reading several spec sheets / docs / articles in one call. Each URL
    gets its own tab, so they do not interfere. Errors per-URL are reported in
    that entry's 'error' key instead of failing the whole batch.

    Args:
        urls: List of http(s) URLs to extract.
        wait_ms: Wait for JS settle before reading (default 2000).
        max_chars: Per-page text cap (default 20000).
        max_links: Per-page link cap (default 50).
    """
    results: list[dict[str, Any]] = []
    for url in urls:
        try:
            _validate_url(url)
            data = await get_client().get_page_text(url, wait_ms=wait_ms)
            if "error" in data:
                results.append({"url": url, "error": data["error"]})
            else:
                text = (data.get("text") or "")[:max_chars]
                links = (data.get("links") or [])[:max_links]
                results.append(
                    {
                        "url": data.get("url", url),
                        "title": data.get("title", ""),
                        "content": text,
                        "charCount": len(text),
                        "links": links,
                        "extracted_with": "camoufox-mcp",
                    }
                )
        except Exception as exc:
            results.append({"url": url, "error": f"extract failed: {exc}"})
    return results


@mcp.tool()
async def search_web(
    query: str,
    categories: str | None = None,
    engines: str | None = None,
    language: str = "en-US",
    pageno: int = 1,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search the web via the self-hosted SearXNG instance and return clean results.

    Best for: finding pages to read (pair with extract_web). Runs against the
    in-stack SearXNG metasearch container, so it is private, free, and has no
    API key. Returns a simplified list of {url, title, content} results plus the
    raw engine response.

    Args:
        query: The search query.
        categories: Optional comma-separated categories (general, news, science,
            files, images, videos, music, it, social_media).
        engines: Optional comma-separated specific search engines to use.
        language: Result language code (default en-US).
        pageno: Result page number (default 1).
        max_results: Max result entries to return (default 10; raise for deep sweep).
    """
    base_url = SEARXNG_BASE_URL.rstrip("/")
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "pageno": pageno,
        "language": language,
    }
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    try:
        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/search", params=params)
            if resp.status_code >= 400:
                return {"error": f"SearXNG search -> HTTP {resp.status_code}: {resp.text[:300]}", "query": query}
            data = resp.json()
    except Exception as exc:
        return {"error": f"search failed: {exc}", "query": query}

    # Normalize SearXNG JSON -> a stable, tool-friendly shape.
    raw_results = data.get("results", []) if isinstance(data, dict) else []
    results: list[dict[str, Any]] = []
    for item in raw_results[:max_results]:
        results.append(
            {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "content": item.get("content", ""),
            }
        )
    return {
        "query": query,
        "numResults": len(results),
        "results": results,
        "engine": data.get("engine", "") if isinstance(data, dict) else "",
        "raw_response": data if isinstance(data, dict) and not results else None,
    }


@mcp.tool()
async def extract_structured(
    url: str,
    schema: dict[str, Any],
    wait_ms: int = 3000,
) -> dict[str, Any]:
    """Scrape structured fields from a page using JSON Schema.

    Pass a JSON Schema object like
    {"type":"object","properties":{"price":{"type":"string"},"title":{"type":"string"}}}
    Optionally give each property an 'x-ref' (accessibility ref) to target an
    element, or rely on the server's auto-detection.

    Args:
        url: The page to scrape.
        schema: JSON Schema (type=object, properties map).
        wait_ms: Wait for JS settle before extracting.
    """
    _validate_url(url)
    client = get_client()
    try:
        await client.start_browser()
        tab_id = await client.create_tab()
        try:
            await client.navigate(tab_id, url)
            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000.0)
            return await client.extract(tab_id, schema)
        finally:
            await client.close_tab(tab_id)
    except Exception as exc:
        return {"error": f"structured extract failed: {exc}", "url": url}


@mcp.tool()
async def browser_snapshot(url: str, wait_ms: int = 3000) -> dict[str, Any]:
    """Open a page and return the accessibility snapshot (element refs).

    Best for: understanding page structure / finding clickable elements before
    driving it with browser interactions. Returns the a11y tree snapshot string.
    """
    _validate_url(url)
    client = get_client()
    try:
        await client.start_browser()
        tab_id = await client.create_tab()
        try:
            await client.navigate(tab_id, url)
            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000.0)
            snap = await client.snapshot(tab_id)
            return {
                "url": snap.get("url", url),
                "snapshot": snap.get("snapshot", ""),
                "refsCount": snap.get("refsCount", 0),
                "truncated": snap.get("truncated", False),
            }
        finally:
            await client.close_tab(tab_id)
    except Exception as exc:
        return {"error": f"snapshot failed: {exc}", "url": url}


@mcp.tool()
async def search_docs(
    library: str,
    query: str,
    version: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search up-to-date documentation for a library/package (docs-mcp-server sidecar).

    Self-hosted replacement for Context7. Returns ranked, version-aware docs
    snippets with markdown content (real code examples, API signatures). Requires
    the 'docs' compose service. Pair with search_web/extract_web for broad web
    research; this is for exact library/API docs.

    Args:
        library: Package/library name (e.g. 'react', 'python', 'openai').
        query:   Documentation search query (e.g. 'hooks lifecycle').
        version: Optional version or X-range (e.g. '18.0.0', '5.x'). Searches
                 the matching version if indexed; otherwise closest available.
        limit:   Max result snippets (default 5).
    """
    try:
        client = DocsMcpClient(DOCS_MCP_BASE_URL, DOCS_MCP_TIMEOUT)
        arguments: dict[str, Any] = {"library": library, "query": query, "limit": limit}
        if version:
            arguments["version"] = version
        result = await client._call_tool("search_docs", arguments)
    except Exception as exc:
        return {"error": f"docs search failed: {exc}", "library": library, "query": query}
    return {
        "library": library,
        "query": query,
        "version": version,
        "numResults": len(result.get("content", [])),
        "content": result.get("content", []),
        "isError": result.get("isError", False),
    }


@mcp.tool()
async def fetch_url(url: str) -> dict[str, Any]:
    """Fetch a URL and convert it to clean Markdown (docs-mcp-server sidecar).

    Read path for docs-indexed pages / general pages returning Markdown, useful
    when you want a plain-text view without launching the stealth browser. This
    is the docs-mcp-server 'fetch_url' tool proxied through kitsune. It differs
    from extract_web (stealth browser + JS) - use this for simple static pages.

    Args:
        url: The http(s) URL to fetch and convert to Markdown.
    """
    _validate_url(url)
    try:
        client = DocsMcpClient(DOCS_MCP_BASE_URL, DOCS_MCP_TIMEOUT)
        result = await client._call_tool("fetch_url", {"url": url})
    except Exception as exc:
        return {"error": f"docs fetch failed: {exc}", "url": url}
    return {
        "url": url,
        "content": result.get("content", []),
        "isError": result.get("isError", False),
    }


@mcp.tool()
async def list_libraries() -> dict[str, Any]:
    """List all libraries currently indexed in the docs service (read-only).

    Discovery helper for the docs-mcp-server sidecar. Use this before
    search_docs to learn which library names are available to query, so you
    do not guess incorrectly and get a 'library not found'. Example output
    includes the library name and its indexed version(s).

    Returns:
        The docs sidecar's list of indexed libraries (name + versions).
    """
    try:
        client = DocsMcpClient(DOCS_MCP_BASE_URL, DOCS_MCP_TIMEOUT)
        result = await client._call_tool("list_libraries", {})
    except Exception as exc:
        return {"error": f"docs list_libraries failed: {exc}"}
    return {
        "content": result.get("content", []),
        "isError": result.get("isError", False),
    }


@mcp.tool()
async def find_version(
    library: str,
    target_version: str | None = None,
) -> dict[str, Any]:
    """Find the best matching indexed version for a library (read-only).

    Version-aware companion to search_docs. Given a library and (optionally)
    a version or X-range (e.g. '18.0.0' or '5.x'), returns the closest
    indexed version(s). Use this to discover which versions of a library are
    available before searching, or to pin a search to a specific version.

    Args:
        library: Package/library name (must be indexed; see list_libraries).
        target_version: Optional version or X-range to match (e.g. '18.0.0',
                 '5.x'). If omitted, returns the latest indexed version.

    Returns:
        The docs sidecar's matching version(s) metadata.
    """
    try:
        client = DocsMcpClient(DOCS_MCP_BASE_URL, DOCS_MCP_TIMEOUT)
        arguments: dict[str, Any] = {"library": library}
        if target_version:
            arguments["targetVersion"] = target_version
        result = await client._call_tool("find_version", arguments)
    except Exception as exc:
        return {"error": f"docs find_version failed: {exc}", "library": library}
    return {
        "library": library,
        "target_version": target_version,
        "content": result.get("content", []),
        "isError": result.get("isError", False),
    }


# ---------------------------------------------------------------------------
# Starlette app: /health + MCP endpoint at /mcp
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield
    client = _client
    if client is not None:
        await client.aclose()


async def health_route(request: Request) -> JSONResponse:
    try:
        camo = await get_client()._client.get(f"{CAMOUFOX_BASE_URL}/health", timeout=10)
        camo_status = camo.json() if camo.status_code == 200 else {"error": f"HTTP {camo.status_code}"}
    except Exception as exc:
        camo_status = {"error": str(exc)}
    try:
        # docs-mcp-server owns /health as its web admin console (HTML); the
        # real MCP surface lives at /mcp. Probe /mcp with a cheap tools/list
        # to confirm the docs sidecar is actually queryable.
        docs_client = DocsMcpClient(DOCS_MCP_BASE_URL, 10)
        await docs_client._call_tool("list_libraries", {})
        docs_status = {"ok": True, "mcp": f"{DOCS_MCP_BASE_URL}/mcp"}
    except Exception as exc:
        docs_status = {"error": str(exc)}
    return JSONResponse(
        {
            "status": "ok",
            "camoufox": camo_status,
            "docs": docs_status,
            "mcp_tools": ["search_web", "extract_web", "extract_web_batch", "extract_structured", "browser_snapshot", "search_docs", "fetch_url", "list_libraries", "find_version"],
            "searxng": SEARXNG_BASE_URL,
        }
    )


async def root_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse("kitsune: MCP streamable-http endpoint at /mcp, health at /health")


app = Starlette(
    routes=[
        Route("/", root_route),
        Route("/health", health_route),
        Mount("/", app=mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=MCP_ENABLE_DNS_REBINDING_PROTECTION,
                allowed_hosts=[h.strip() for h in MCP_ALLOWED_HOSTS.split(",") if h.strip()],
                allowed_origins=[o.strip() for o in MCP_ALLOWED_ORIGINS.split(",") if o.strip()],
            )
        )),
    ],
    lifespan=lifespan,
)


def main() -> None:
    """Run the streamable-HTTP server (for standalone / network hosting)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)


async def main_stdio() -> None:
    """Run over stdio (spawned by an MCP client like Hermes)."""
    await mcp.run_stdio_async()


def main_stdio_entry() -> None:
    """Console-script entry point for stdio mode (no event loop tricks needed)."""
    asyncio.run(main_stdio())


if __name__ == "__main__":
    if os.environ.get("MCP_STDIO") == "1":
        main_stdio_entry()
    else:
        main()
