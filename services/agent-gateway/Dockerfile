FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c
WORKDIR /app
RUN mkdir /app/state && chown node:node /app/state
COPY --chown=node:node *.mjs ./
USER node
ENV NODE_ENV=production GATEWAY_STATE_PATH=/app/state/gateway.sqlite
CMD ["node", "server.mjs"]
