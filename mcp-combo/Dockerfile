# syntax=docker/dockerfile:1.4
# ---------------------------------------------------------------------------
# kitsune - MCP web research gateway (SearXNG search + Camoufox extract + docs)
#
# Runs the STREAMABLE-HTTP variant (uvicorn) so the container can be reached
# over the LAN / by other machines. The stdio variant (used by Hermes) runs
# locally via `uv run kitsune-stdio` - it is NOT needed in this image.
#
# Build:  docker build -t kitsune .
# Run:    docker run -d --name kitsune -p 8095:8095 \
#           -e CAMOUFOX_BASE_URL=http://camofox:9377 kitsune
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# uv - the fast Python package manager (installs the project + builds console scripts)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (layer cache) - pyproject + uv.lock define the env
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the package source and install it (creates `kitsune` console script)
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Non-root user for a bit of defense in depth
RUN useradd -m -u 1001 mcpuser
USER mcpuser

# uv installs into /app/.venv - prepend its bin so `kitsune` resolves.
ENV PATH="/app/.venv/bin:$PATH" \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8095 \
    MCP_ALLOWED_HOSTS="*" \
    MCP_ALLOWED_ORIGINS=""

EXPOSE 8095

# HTTP server by default (the container needs to be reachable, not stdio)
CMD ["kitsune"]
