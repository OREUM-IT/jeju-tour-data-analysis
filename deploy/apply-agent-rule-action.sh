#!/usr/bin/env bash
# Switch the stored Gateway flow between the reviewed 8-call Vertex build and the
# rule-action build (OpenAI gpt-5.4-mini picks place IDs only; the MCP composes
# action, verdicts and notices from catalog evidence). Apply first updates the MCP
# image, because only the new MCP accepts {"placeIds": [...]} selections, then
# switches the flow and recreates only the Gateway to pin it. No model invocation,
# request submission or cancellation, API key rotation or Langflow restart.
#
# Resumable like apply-agent-call-budget.sh: prepare-gateway.py writes
# .local/.gateway-flow-change before Langflow can change and this helper clears it
# only after every check passes. --rollback switches back to the 8-call Vertex
# flow and keeps the new MCP, which still validates full responses unchanged.
set +x
set -euo pipefail
mode=apply
if [[ $# -eq 1 && "$1" == '--verify-only' ]]; then
  mode=verify
elif [[ $# -eq 1 && "$1" == '--rollback' ]]; then
  mode=rollback
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash deploy/apply-agent-rule-action.sh [--verify-only|--rollback]' >&2
  exit 2
fi
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
failed() {
  echo "AGENT_RULE_ACTION_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2
  if [[ "$phase" != config ]] && python3 prepare-gateway.py --flow-change-pending >/dev/null 2>&1; then
    echo '흐름 변경이 끝나지 않았습니다. 같은 명령을 다시 실행하면 이어서 마무리합니다. 되돌리려면 --rollback으로 실행합니다.' >&2
  fi
}
trap failed ERR
"${compose[@]}" config --quiet

phase=resume_check
if python3 prepare-gateway.py --flow-change-pending; then
  pending=1
else
  result=$?
  if [[ "$result" != 3 ]]; then
    echo '이전 흐름 변경 상태를 확인하지 못해 중단했습니다. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
    exit "$result"
  fi
  pending=0
fi

# Read-only observation, not an atomic maintenance lock: run while web
# submissions are quiet. Exits on anything but a confirmed idle state.
idle_gate() {
  phase=$1
  if python3 check-agent-queue-idle.py; then
    return 0
  else
    result=$?
  fi
  if [[ "$result" == 75 ]]; then
    echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 직접 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 더 바꾸지 않았습니다.' >&2
    exit 75
  fi
  if [[ "$result" == 76 ]]; then
    echo 'Gateway가 흐름 불일치(FLOW_CHANGED)로 새 요청을 거부하고 있습니다. Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
  elif [[ "$pending" == 1 ]]; then
    echo '이전 흐름 변경이 끝나지 않았습니다. 공개 준비 확인 대신 Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
  else
    echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 이번 실행에서는 흐름과 서비스를 더 바꾸지 않았습니다.' >&2
    exit "$result"
  fi
  phase="resume_$1"
  if python3 check-agent-queue-idle.py --resume; then
    return 0
  else
    result=$?
  fi
  if [[ "$result" == 75 ]]; then
    echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 더 바꾸지 않았습니다.' >&2
  else
    echo 'Gateway 내부 작업 수 또는 MCP 실행 상태를 확인하지 못했습니다. 원인을 확인한 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 더 바꾸지 않았습니다.' >&2
  fi
  exit "$result"
}

if [[ "$mode" == verify ]]; then
  if [[ "$pending" == 1 ]]; then
    echo '이전 적용이 끝나지 않았습니다. 같은 적용 명령 또는 --rollback을 다시 실행해 마무리해 주세요.' >&2
    exit 1
  fi
elif [[ "$mode" == rollback ]]; then
  idle_gate idle_check
  phase=flow_change
  python3 prepare-gateway.py --rule-action-rollback-flow
  phase=recreate_gateway
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 agent-gateway
else
  # Build while the existing MCP keeps serving.
  phase=build_mcp
  "${compose[@]}" build jeju-mcp
  idle_gate idle_check
  phase=recreate_mcp
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 90 jeju-mcp
  phase=offline_rule_check
  "${compose[@]}" exec -T jeju-mcp node --input-type=module - < tests/check-rule-action.mjs
  # The MCP restart dropped no live scope, but new work may have arrived since.
  idle_gate idle_before_flow_change
  phase=flow_change
  python3 prepare-gateway.py --rule-action-flow
  phase=recreate_gateway
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 agent-gateway
fi
phase=flow_check
if [[ "$mode" == rollback ]]; then
  python3 prepare-gateway.py --check-flow
else
  python3 prepare-gateway.py --check-rule-action-flow
fi
phase=public_readiness
python3 tunnel/check.py --scope public
phase=queue_readiness
python3 check-agent-queue-idle.py --queue-ready
if [[ "$mode" != verify ]]; then
  phase=complete
  python3 prepare-gateway.py --complete-flow-change
fi
case "$mode" in
  verify) printf '%s\n' '{"status":"passed","scope":"agent-rule-action-verified","model":"gpt-5.4-mini","servicesRestarted":false,"modelExecuted":false,"productionReady":false}' ;;
  rollback) printf '%s\n' '{"status":"passed","scope":"agent-rule-action-rolled-back","model":"gemini-3.8-flash","modelCallLimit":8,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  *) printf '%s\n' '{"status":"passed","scope":"agent-rule-action-applied","model":"gpt-5.4-mini","modelCallLimit":8,"services":["jeju-mcp","agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
esac
