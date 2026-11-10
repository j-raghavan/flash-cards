# SPDX-License-Identifier: AGPL-3.0-only
# The web app: built with Node, served by Caddy together with a reverse proxy to
# the API under /api (see apps/web/Caddyfile). Build from the repository root:
#
#   docker build -f apps/web/Dockerfile -t flash-cards-web .

FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM caddy:2-alpine
COPY apps/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
# Caddy needs writable data and config directories even with automatic HTTPS
# and the admin API off; /tmp keeps the image usable with a read-only root.
ENV XDG_DATA_HOME=/tmp/caddy/data \
    XDG_CONFIG_HOME=/tmp/caddy/config
# Readable by the unprivileged user whatever the build host's permissions were:
# a checkout on a Synology share, for example, can be owner-only (mode 700).
RUN chmod -R a+rX /srv /etc/caddy
# Unprivileged: the site listens on 8080, so no root and no extra capabilities.
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/index.html || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
