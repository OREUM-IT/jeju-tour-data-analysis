#!/usr/bin/env bash
# Add the overtourism diagnosis service to the running Gateway stack: a new MCP
# image (diagnosis scopes, indicator tools and final validation), the separate
# Langflow diagnosis flow derived from the reviewed rule-action build
# (OpenAI gpt-5.4-mini, same Credential JEJU_OPENAI_API_KEY), and a Gateway image
# that pins that flow for POST /v1/overtourism. The recommendation flow, its pin,
# keys and queue limits are unchanged. No model invocation, request submission
# or cancellation, API key creation or rotation, or Langflow restart.
#
#   bash deploy/apply-overtourism-agent.sh                 # apply; rerun to resume after a failure
#   bash deploy/apply-overtourism-agent.sh --verify-only   # checks only, no service change
#   bash deploy/apply-overtourism-agent.sh --replace-flow  # switch a stored diagnosis flow to this build
#   bash deploy/apply-overtourism-agent.sh --disable       # remove only the pin; the route answers unavailable
set +x
set -euo pipefail
mode=apply
if [[ $# -eq 1 && "$1" == '--verify-only' ]]; then
  mode=verify
elif [[ $# -eq 1 && "$1" == '--replace-flow' ]]; then
  mode=replace
elif [[ $# -eq 1 && "$1" == '--disable' ]]; then
  mode=disable
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash deploy/apply-overtourism-agent.sh [--verify-only|--replace-flow|--disable]' >&2
  exit 2
fi
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
trap 'echo "OVERTOURISM_AGENT_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2' ERR
"${compose[@]}" config --quiet

# Read-only observation, not an atomic maintenance lock: run while web
# submissions are quiet. Recreating the MCP or Gateway drops their RAM work.
idle_gate() {
  phase=$1
  if python3 check-agent-queue-idle.py; then
    return 0
  else
    result=$?
  fi
  if [[ "$result" == 75 ]]; then
    echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 서비스를 더 바꾸지 않았습니다.' >&2
  else
    echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 이번 실행에서는 서비스를 더 바꾸지 않았습니다.' >&2
  fi
  exit "$result"
}
recreate_gateway() {
  phase=recreate_gateway
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 agent-gateway
}

case "$mode" in
  apply)
    # Build while the existing services keep serving.
    phase=build
    "${compose[@]}" build jeju-mcp agent-gateway
    idle_gate idle_check
    phase=recreate_mcp
    "${compose[@]}" up -d --no-deps --wait --wait-timeout 90 jeju-mcp
    phase=offline_check
    "${compose[@]}" exec -T jeju-mcp node --input-type=module - < tests/check-overtourism.mjs
    # Creates the flow only when missing; an edited or older stored flow stops here (use --replace-flow).
    phase=flow
    python3 prepare-overtourism.py --provision
    idle_gate idle_before_gateway
    recreate_gateway
    ;;
  replace)
    phase=flow
    python3 prepare-overtourism.py --replace
    idle_gate idle_before_gateway
    recreate_gateway
    ;;
  disable)
    idle_gate idle_check
    phase=disable
    python3 prepare-overtourism.py --disable
    recreate_gateway
    ;;
esac
if [[ "$mode" != disable ]]; then
  phase=flow_check
  python3 prepare-overtourism.py --check
  phase=route_check
  "${compose[@]}" exec -T agent-gateway node /app/check.mjs --internal --overtourism
fi
phase=public_readiness
python3 tunnel/check.py --scope public
phase=queue_readiness
python3 check-agent-queue-idle.py --queue-ready
case "$mode" in
  verify) printf '%s\n' '{"status":"passed","scope":"overtourism-agent-verified","model":"gpt-5.4-mini","servicesRestarted":false,"modelExecuted":false,"productionReady":false}' ;;
  replace) printf '%s\n' '{"status":"passed","scope":"overtourism-flow-replaced","model":"gpt-5.4-mini","services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  disable) printf '%s\n' '{"status":"passed","scope":"overtourism-agent-disabled","services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  *) printf '%s\n' '{"status":"passed","scope":"overtourism-agent-applied","model":"gpt-5.4-mini","modelCallLimit":8,"services":["jeju-mcp","agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
esac
