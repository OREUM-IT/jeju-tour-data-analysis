# Retrieved and smoke-tested on 2026-11-19; update digest only after review.
FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY --chown=node:node src/ ./src/
COPY --chown=node:node fixtures/catalog.json ./fixtures/catalog.json
COPY --chown=node:node scripts/check-client.mjs ./scripts/check-client.mjs
COPY --chown=node:node scripts/check-v2.mjs ./scripts/check-v2.mjs
USER node
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8788
CMD ["node", "src/server.mjs"]
