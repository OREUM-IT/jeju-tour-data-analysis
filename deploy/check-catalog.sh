#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f compose.yaml -f compose.catalog.yaml exec -T jeju-mcp-v2 node scripts/check-v2.mjs
