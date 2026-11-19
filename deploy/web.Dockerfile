# Dashboard (React build) served by Caddy, which also terminates HTTPS
# (automatic Let's Encrypt certificates) and proxies /api to the backend.
FROM node:20-bookworm-slim AS build
WORKDIR /src
COPY frontend/package.json frontend/yarn.lock ./
RUN corepack enable && yarn install --frozen-lockfile --network-timeout 600000
COPY frontend/ .
# Same origin as the API (Caddy routes /api) — no backend URL baked into the build.
ENV REACT_APP_BACKEND_URL="" GENERATE_SOURCEMAP=false CI=false
RUN yarn build

FROM caddy:2.8-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/build /srv
