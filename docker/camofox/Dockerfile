# =============================================================================
# mcp_kitsune camofox image - clone upstream on the fly (no vendored source)
#
# The camoufox-browser source (jo-inc/camofox-browser) is 101% pristine
# upstream in our repo - we ship ZERO local patches to it. So instead of
# vendoring 191+ upstream files into the repo, clone the exact pinned tag at
# image-build time. Builds stay deterministic: the version is pinned.
#
# Build context = REPO ROOT (./), not a camoufox subfolder.
#
# NOTE: As of v1.15.0, upstream Camoufox includes:
#   - Graceful Xvfb fallback (try/catch → headless mode) — our old
#     createVirtualDisplay patch is no longer needed.
#   - Session-isolation fixes: a navigation timeout no longer destroys the
#     whole user session, and stale-file cleanup is protected. This fixes
#     the persistence-corruption cascade.
#   - Signal handling (handleSIGTERM/SIGHUP disabled) prevents browser-restart
#     races with the persistence plugin.
#
# Our old patches (docker/camofox/patches/server.patch) fixed:
#   1. WebGL fingerprint probe segfault → fixed in upstream v1.15.0+ (sampleWebGL
#      removed/updated)
#   2. Xvfb segfault → fixed in upstream v1.17.0 (graceful fallback to headless)
# Both patches are now obsolete and NOT applied.
# =============================================================================

# --- Stage 0: clone upstream source at the pinned tag ------------------------
FROM docker.io/alpine/git:latest AS clone
ARG CAMOFOX_UPSTREAM_TAG=v1.17.0
RUN git clone --depth 1 --branch "${CAMOFOX_UPSTREAM_TAG}" \
      https://github.com/jo-inc/camofox-browser.git /src

# --- Stage 1: runtime image --------------------------------------------------
# v1.17.0 depends on better-sqlite3 which ships an arm64 prebuild linked
# against GLIBC_2.38 — bookworm (glibc 2.36) crashes at runtime. Trixie
# (glibc 2.41) satisfies the requirement. amd64 is unaffected but uses the
# same image for consistency.
FROM node:22-trixie-slim AS camofox-browser

# Pinned Camoufox version for reproducible builds
ARG CAMOUFOX_VERSION=152.0.4
ARG CAMOUFOX_RELEASE=beta.28
ARG TARGETARCH
# Map TARGETARCH to the architecture string used in the download URL
RUN case "${TARGETARCH}" in \
      amd64) echo "x86_64" ;; \
      arm64) echo "arm64" ;; \
      *) echo "Unsupported arch: ${TARGETARCH}" && exit 1 ;; \
    esac > /tmp/arch
# Re-import as an ARG so it is available in subsequent RUN layers
ARG ARCH="x86_64"
ARG YT_DLP_VERSION=2026.08.19
ARG YT_DLP_SHA256=1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6

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
    # Without these, Firefox cannot create WebGL contexts -- a major bot detection signal
    # libegl1 -- named libegl1-mesa on bookworm, dropped in trixie
    libegl1 \
    libgl1-mesa-dri \
    libgbm1 \
    # Xvfb virtual display -- runs Camoufox as if on a real desktop (better anti-detection)
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
    && rm -rf /var/lib/apt/lists/*

# Pre-bake Camoufox browser binary into image (downloaded at build time)
# Note: unzip returns exit code 1 for warnings (Unicode filenames), so we use || true and verify
# -f so a 404 fails here instead of writing "Not Found" into the .zip: without it the
# build dies three commands later on "unzip: cannot find zipfile directory", which
# points at the archive rather than at the URL that was actually wrong.
RUN mkdir -p /root/.cache/camoufox \
    && curl -fL -o /tmp/camoufox.zip "https://github.com/daijro/camoufox/releases/download/v${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}/camoufox-${CAMOUFOX_VERSION}-${CAMOUFOX_RELEASE}-lin.${ARCH}.zip" \
    && (unzip -q /tmp/camoufox.zip -d /root/.cache/camoufox || true) \
    && rm /tmp/camoufox.zip \
    && chmod -R 755 /root/.cache/camoufox \
    && echo "{\"version\":\"${CAMOUFOX_VERSION}\",\"release\":\"${CAMOUFOX_RELEASE}\"}" > /root/.cache/camoufox/version.json \
    && test -f /root/.cache/camoufox/camoufox-bin && echo "Camoufox installed successfully"

WORKDIR /app

# Copy the application source from the clone stage
COPY --from=clone /src/package.json /src/package-lock.json ./
COPY --from=clone /src/scripts/ ./scripts/
# better-sqlite3 has no prebuild matching this node/arch, so npm ci falls back to
# `node-gyp rebuild`, which fails on node:*-slim with "Error: not found: make".
# Install a toolchain for the build and purge it in the same layer so it does not
# land in the image. Independent of the glibc issue noted at the FROM line: this
# one fails at build time on any Debian release, that one at runtime on bookworm.
#
# --ignore-scripts: postinstall.js imports lib/camoufox-download.js which isn't
#   on disk yet (it's copied in the next COPY batch). We skip the postinstall
#   hook here and run it manually after all source files are present, with
#   CAMOFOX_SKIP_DOWNLOAD=1 (we pre-baked the browser binary in the layer above).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential python3 \
    && npm ci --omit=dev --ignore-scripts \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=clone /src/server.js ./
COPY --from=clone /src/camofox.config.json ./
COPY --from=clone /src/lib/ ./lib/
COPY --from=clone /src/plugins/ ./plugins/
COPY --from=clone /src/scripts/ ./scripts/
COPY --from=clone /src/mcp/ ./mcp/

# Install default plugin dependencies
RUN sh scripts/install-plugin-deps.sh

# yt-dlp: install the same pinned version as upstream v1.17.0.
RUN curl -fL -o /usr/local/bin/yt-dlp "https://github.com/yt-dlp/yt-dlp/releases/download/${YT_DLP_VERSION}/yt-dlp_linux" \
    && chmod 755 /usr/local/bin/yt-dlp

ENV NODE_ENV=production
ENV CAMOFOX_PORT=9377

EXPOSE 9377

CMD ["sh", "-c", "node --max-old-space-size=${MAX_OLD_SPACE_SIZE:-128} server.js"]
