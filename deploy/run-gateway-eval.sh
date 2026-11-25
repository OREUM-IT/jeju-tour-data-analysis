#!/usr/bin/env bash
# Gemini vs GPT on the same 96 held-out questions through the deployed Agent Gateway (docs/35).
#
#   sudo bash deploy/run-gateway-eval.sh prepare                 # held-out set + plan (no network, no model)
#   sudo bash deploy/run-gateway-eval.sh requests <label>         # Gateway payloads for one collection
#   sudo bash deploy/run-gateway-eval.sh reference                # no-model reference report (offline, no model)
#   sudo bash deploy/run-gateway-eval.sh collect <label> [--limit N] [--hourly-budget N]   # PAID: model calls
#   sudo bash deploy/run-gateway-eval.sh score <label> <model> <flow>                      # aggregate report
#
# Node steps run in a throwaway container of the jeju-mcp image that is already built on this server:
# no network, read-only repository code, only deploy/.local/gateway-eval writable. Questions, gold answers,
# requests and responses stay in deploy/.local/gateway-eval. Only deploy/gateway-eval-reports/*.json may be shared.
set -euo pipefail
umask 077

DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DEPLOY/.." && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
IMAGE="$PROJECT-jeju-mcp"
CATALOG="$DEPLOY/.local/catalog/agent-catalog-b0602a1.json"
WORK="$DEPLOY/.local/gateway-eval"
REPORTS="$DEPLOY/gateway-eval-reports"
LABEL_RE='^[a-z0-9][a-z0-9-]{0,40}$'

fail() { echo "{\"status\":\"failed\",\"code\":\"$1\",\"productionReady\":false}" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail ROOT_REQUIRED
[ -f "$CATALOG" ] || fail CATALOG_MISSING
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail MCP_IMAGE_MISSING
install -d -m 0700 "$WORK"
install -d -m 0755 "$REPORTS"

node_step() {
  docker run --rm --network none --user 0:0 --read-only --tmpfs /tmp:size=16m \
    --cap-drop ALL --security-opt no-new-privileges:true --memory 768m --pids-limit 64 \
    -v "$REPO/services/jeju-mcp/src:/app/src:ro" \
    -v "$REPO/services/jeju-mcp/scripts:/app/scripts:ro" \
    -v "$REPO/data:/data:ro" \
    -v "$CATALOG:/app/.local/catalog.json:ro" \
    -v "$WORK:/app/.local/gateway-eval" \
    -v "$REPORTS:/reports" \
    -w /app --entrypoint node "$IMAGE" scripts/gateway-eval.mjs "$@" --catalog .local/catalog.json --dir .local/gateway-eval
}

command="${1:-}"; shift || true
case "$command" in
  prepare)
    [ "$#" -eq 0 ] || fail ARGUMENTS_INVALID
    node_step prepare ;;
  requests)
    [ "$#" -eq 1 ] && [[ "$1" =~ $LABEL_RE ]] || fail ARGUMENTS_INVALID
    node_step requests --label "$1" ;;
  reference)
    [ "$#" -eq 0 ] || fail ARGUMENTS_INVALID
    node_step requests --label reference
    node_step reference --label reference
    node_step score --label reference --model no-model-first-3 --flow rule-action --reports /reports
    chmod 0644 "$REPORTS/reference-report.json" "$REPORTS/reference-collection.json" ;;
  collect)
    [ "$#" -ge 1 ] && [[ "$1" =~ $LABEL_RE ]] || fail ARGUMENTS_INVALID
    label="$1"; shift
    cd "$DEPLOY"
    python3 -I collect-gateway-eval.py --execute-model --label "$label" "$@" ;;
  score)
    [ "$#" -eq 3 ] && [[ "$1" =~ $LABEL_RE ]] && [[ "$2" =~ ^[A-Za-z0-9._:/-]{1,80}$ ]] && [[ "$3" =~ ^[a-z0-9-]{1,40}$ ]] || fail ARGUMENTS_INVALID
    node_step score --label "$1" --model "$2" --flow "$3" --reports /reports
    chmod 0644 "$REPORTS/$1-report.json" "$REPORTS/$1-collection.json" ;;
  *)
    fail ARGUMENTS_INVALID ;;
esac
