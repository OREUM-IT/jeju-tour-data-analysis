FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /builder
COPY --chmod=644 data/theme-category-map.json data/persona-prototypes.json ./data/
COPY --chmod=644 services/jeju-mcp/src/agent-catalog.mjs ./services/jeju-mcp/src/
COPY --chmod=644 services/jeju-mcp/scripts/dump-app-places.mjs services/jeju-mcp/scripts/export-agent-catalog.mjs services/jeju-mcp/scripts/import-catalog.mjs ./services/jeju-mcp/scripts/
COPY --chmod=644 deploy/catalog/generate.sh ./generate.sh
RUN chmod 755 /builder /builder/data /builder/services /builder/services/jeju-mcp /builder/services/jeju-mcp/src /builder/services/jeju-mcp/scripts
USER node
ENTRYPOINT ["bash", "/builder/generate.sh"]
