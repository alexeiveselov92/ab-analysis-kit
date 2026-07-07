# abkit website — static Astro/Starlight build served by nginx.
#
# ⚠ Build context MUST be the repo ROOT: the site is an npm workspace (root
# lockfile + web/ + website/) and sync-docs.mjs imports ../docs, ../CHANGELOG.md
# and ../.claude/rules as the single source of truth.
#   docker build -f website/Dockerfile -t ab-analysis-kit-web .
#
# Presence of this file on `main` is the live-deploy trigger (website.yml builds
# + pushes ghcr.io/alexeiveselov92/ab-analysis-kit-web:latest and dispatches the
# pipelab deploy) — see docs/specs/m6-implementation-plan.md §GATED (G2).

# ---- build ----
FROM node:22-alpine AS build
WORKDIR /app
# Workspace manifests first, so `npm ci` is a cacheable layer independent of sources.
COPY package.json package-lock.json ./
COPY web/package.json ./web/
COPY website/package.json ./website/
RUN npm ci
# Doc sources (single source of truth; sync-docs.mjs renders them into the site).
COPY docs ./docs
COPY CHANGELOG.md ./CHANGELOG.md
COPY .claude/rules ./.claude/rules
# Renderer core — the landing demo imports web/src/shared/chart.ts (workspace dep).
COPY web/src ./web/src
# Site sources (no node_modules / dist — keeps the layer slim and cacheable).
COPY website/astro.config.mjs website/tsconfig.json ./website/
COPY website/scripts ./website/scripts
COPY website/src ./website/src
COPY website/public ./website/public
RUN npm run build --workspace website

# ---- serve ----
FROM nginx:alpine
COPY website/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/website/dist /usr/share/nginx/html
EXPOSE 80
# 127.0.0.1 (not localhost): nginx listens on IPv4 :80 only; localhost can resolve
# to IPv6 ::1 → a false "unhealthy".
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
