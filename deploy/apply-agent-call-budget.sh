#!/usr/bin/env bash
# Switch the stored Gateway flow between the reviewed 4-call and 8-call builds and
# restart only the Gateway to pin it. No model invocation, request submission or
# cancellation, API key rotation, Langflow restart or MCP/Tunnel change.
set +x
set -euo pipefail
mode=apply
if [[ $# -eq 1 && "$1" == '--verify-only' ]]; then
  mode=verify
elif [[ $# -eq 1 && "$1" == '--rollback' ]]; then
  mode=rollback
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash deploy/apply-agent-call-budget.sh [--verify-only|--rollback]' >&2
  exit 2
fi
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
trap 'echo "AGENT_CALL_BUDGET_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2' ERR
"${compose[@]}" config --quiet

if [[ "$mode" != verify ]]; then
  # This read-only observation is not an atomic maintenance lock: run it while
  # new web submissions are paused or quiet. Nothing changes if work is active.
  phase=idle_check
  if python3 check-agent-queue-idle.py; then
    :
  else
    result=$?
    if [[ "$result" == 75 ]]; then
      echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 직접 다시 실행해 주세요. 흐름과 서비스를 바꾸지 않았습니다.' >&2
    else
      echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 흐름과 서비스를 바꾸지 않았습니다.' >&2
    fi
    exit "$result"
  fi
  # From here until the Gateway restarts, it refuses new work (FLOW_CHANGED)
  # instead of running an unpinned graph. Rerunning this script is safe.
  phase=flow_change
  if [[ "$mode" == rollback ]]; then
    python3 prepare-gateway.py --rollback-flow
  else
    python3 prepare-gateway.py --update-flow
  fi
  phase=recreate_gateway
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 agent-gateway
fi
if [[ "$mode" != rollback ]]; then
  phase=flow_check
  python3 prepare-gateway.py --check-flow
fi
phase=public_readiness
python3 tunnel/check.py --scope public
phase=queue_readiness
python3 check-agent-queue-idle.py --queue-ready
case "$mode" in
  verify) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-verified","modelCallLimit":8,"servicesRestarted":false,"modelExecuted":false,"productionReady":false}' ;;
  rollback) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-rolled-back","modelCallLimit":4,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  *) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-applied","modelCallLimit":8,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
esac
