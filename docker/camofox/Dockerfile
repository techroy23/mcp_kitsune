# =============================================================================
# mcp_kitsune camofox image - clone upstream on the fly (no vendored source)
#
# The camofox-browser source (jo-inc/camofox-browser) is 100% pristine
# upstream in our repo - we ship ZERO local patches to it (the anti-detection
# config lives in docker-compose.yml env vars, not in the source). So instead
# of vendoring 191 upstream files into the repo, clone the exact pinned tag at
# image-build time. Builds stay determininistic: the version is pinned to the
# same tag we were vendoring (v1.13.1 = npm 1.13.1).
#
# Build context = REPO ROOT (./), not a camofox subfolder.
#
# The browser binary (.zip) is downloaded in stage-2 exactly as the old
# Dockerfile.ci did - pinned by CAMOUFOX_VERSION / CAMOUFOX_RELEASE args.
# =============================================================================

# --- Stage 0: clone upstream source at the pinned tag + apply our patches ---
FROM docker.io/alpine/git:latest AS clone
# Patch tool
RUN apk add --no-cache patch
ARG CAMOFOX_UPSTREAM_TAG=v1.13.1
RUN git clone --depth 1 --branch "${CAMOFOX_UPSTREAM_TAG}" \
      https://github.com/jo-inc/camofox-browser.git /src

# Re-apply our stability patches. The upstream source ships a virtual-display
# path and a WebGL fingerprint probe that BOTH segfault the Camoufox binary in
# this container (missing dbus / XDG_RUNTIME_DIR; sampleWebGL via imit crashes
# without a proper display). Our patches force headless mode and disable the
# WebGL probe, which is the working config we validated. See
# patches/server.patch for the exact hunks.
# patches/server.patch lives in the repo under docker/camofox/patches/ (build
# context is the repo root, so the path includes the docker/camofox prefix).
COPY docker/camofox/patches/server.patch /tmp/server.patch
RUN cd /src && patch -p1 < /tmp/server.patch

# --- Stage 1: runtime image ------------------------------------------------
FROM node:20-slim

ARG CAMOUFOX_VERSION=135.0.1
ARG CAMOUFOX_RELEASE=beta.24
ARG TARGETARCH

# Install dependencies for Camoufox (Firefox-based)
RUN apt-get update && apt-get install -y \
    # Firefox dependencies
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libasound2 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    # Mesa OpenGL/EGL for WebGL support (software rendering via llvmpipe)
    libegl1-mesa \
    libgl1-mesa-dri \
    libgbm1 \
    # Xvfb virtual display
    xvfb \
    # Fonts
    fonts-liberation \
    fonts-noto-color-emoji \
    fontconfig \
    # Utils
    ca-certificates \
    curl \
    unzip \
    # yt-dlp runtime dependency
    python3-minimal \
    # Native module build tools: better-sqlite3 has no prebuilt binary for
    # node v20.20.2, so npm ci triggers a node-gyp compile. slim images lack
    # make/g++/python3 headers -> the build fails without these.
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Download and install the Camoufox browser binary
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) CAMOUFOX_ARCH="x86_64"; YTDLP_SUFFIX="" ;; \
      arm64) CAMOUFOX_ARCH="arm64";   YTDLP_SUFFIX="_aarch64" ;; \
      *) echo "Unsupported arch: ${TARGETARCH}" && exit 1 ;; \
    esac; \
    mkdir -p /root/.cache/camoufox; \
    curl -fSL "https://github.com/daijro/camoufox/releases/download/v${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}/camoufox-${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}-lin.${CAMOUFOX_ARCH}.zip" \
      -o /tmp/camoufox.zip; \
    (unzip -q /tmp/camoufox.zip -d /root/.cache/camoufox || true); \
    chmod -R 755 /root/.cache/camoufox; \
    # camofox-browser expects the fontconfig dir named `fontconfig` (camoufox-js
    # compatibility). Upstream Camoufox ships it as `fontconfigs`. Symlink it.
    ln -sfn /root/.cache/camoufox/fontconfigs /root/.cache/camoufox/fontconfig; \
    echo "{\"version\":\"${CAMOUFOX_VERSION}\",\"release\":\"${CAMOUFOX_RELEASE}\"}" > /root/.cache/camoufox/version.json; \
    test -f /root/.cache/camoufox/camoufox-bin && echo "Camoufox installed successfully"; \
    rm /tmp/camoufox.zip; \
    curl -fSL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux${YTDLP_SUFFIX}" \
      -o /usr/local/bin/yt-dlp; \
    chmod 755 /usr/local/bin/yt-dlp

WORKDIR /app

# Copy the application source from the clone stage
COPY --from=clone /src/package.json /src/package-lock.json ./
COPY --from=clone /src/scripts/ ./scripts/
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --production

COPY --from=clone /src/server.js ./
COPY --from=clone /src/camofox.config.json ./
COPY --from=clone /src/lib/ ./lib/
COPY --from=clone /src/plugins/ ./plugins/
COPY --from=clone /src/scripts/ ./scripts/
# Copy the bundled mcp/ server. The persistence plugin imports from
# /app/mcp/lib/cookies.mjs, so this dir MUST be present or plugin load fails.
COPY --from=clone /src/mcp/ ./mcp/

# Install default plugin dependencies
# sh is used explicitly because COPY from Windows does not preserve +x bits.
RUN sh scripts/install-plugin-deps.sh

ENV NODE_ENV=production
ENV CAMOFOX_PORT=9377

EXPOSE 9377

CMD ["sh", "-c", "node --max-old-space-size=${MAX_OLD_SPACE_SIZE:-128} server.js"]