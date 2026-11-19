#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
test -f .local/catalog/agent-catalog.json || { echo 'Run prepare-catalog.sh first.'; exit 1; }
test -f .local/v2.env || { echo 'Run python3 deploy/prepare.py first.'; exit 1; }
docker compose -f compose.yaml -f compose.catalog.yaml config --quiet
docker compose -f compose.yaml -f compose.catalog.yaml up -d --build --no-deps --wait --wait-timeout 120 jeju-mcp-v2
