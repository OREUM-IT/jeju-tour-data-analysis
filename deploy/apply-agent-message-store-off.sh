#!/usr/bin/env bash
# Stop storing Gateway recommendation runs in Langflow's message table, then
# recreate only the Gateway to pin the changed flow. Works on whichever reviewed
# build is stored (8-call Vertex or rule-action gpt-5.4-mini) and keeps it: only
# should_store_message of Chat Input and Chat Output changes, so new runs no
# longer write the request JSON (survey text) and the final output there. Native
# tracing (trace/span) stays on for the latency/token SQL; older rows are
# redacted separately and only on request (deploy/redact-agent-history.py).
# No model invocation, request submission or cancellation, API key rotation, or
# Langflow/MCP/DB/Tunnel restart.
#
#   bash deploy/apply-agent-message-store-off.sh                # apply; rerun to resume after a failure
#   bash deploy/apply-agent-message-store-off.sh --verify-only  # checks only, no service change
#   bash deploy/apply-agent-message-store-off.sh --rollback     # store messages again, same build
#
# Resumable like apply-agent-call-budget.sh: prepare-gateway.py writes
# .local/.gateway-flow-change before Langflow can change and this helper clears it
# only after every check passes.
set +x
set -euo pipefail
mode=apply
if [[ $# -eq 1 && "$1" == '--verify-only' ]]; then
  mode=verify
elif [[ $# -eq 1 && "$1" == '--rollback' ]]; then
  mode=rollback
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash deploy/apply-agent-message-store-off.sh [--verify-only|--rollback]' >&2
  exit 2
fi
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
failed() {
  echo "AGENT_MESSAGE_STORE_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2
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
    echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 직접 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
    exit 75
  fi
  if [[ "$result" == 76 ]]; then
    echo 'Gateway가 흐름 불일치(FLOW_CHANGED)로 새 요청을 거부하고 있습니다. Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
  elif [[ "$pending" == 1 ]]; then
    echo '이전 흐름 변경이 끝나지 않았습니다. 공개 준비 확인 대신 Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
  else
    echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
    exit "$result"
  fi
  phase="resume_$1"
  if python3 check-agent-queue-idle.py --resume; then
    return 0
  else
    result=$?
  fi
  if [[ "$result" == 75 ]]; then
    echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
  else
    echo 'Gateway 내부 작업 수 또는 MCP 실행 상태를 확인하지 못했습니다. 원인을 확인한 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
  fi
  exit "$result"
}

if [[ "$mode" == verify ]]; then
  if [[ "$pending" == 1 ]]; then
    echo '이전 적용이 끝나지 않았습니다. 같은 적용 명령 또는 --rollback을 다시 실행해 마무리해 주세요.' >&2
    exit 1
  fi
else
  idle_gate idle_check
  # Accepts only the exact reviewed 8-call or rule-action graph (or its other
  # storage variant); anything else stops before a PATCH with GATEWAY_EXISTING_FLOW_DIFFERS.
  phase=flow_change
  if [[ "$mode" == rollback ]]; then
    python3 prepare-gateway.py --message-store-on-flow
  else
    python3 prepare-gateway.py --message-store-off-flow
  fi
  phase=recreate_gateway
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 agent-gateway
fi
phase=flow_check
if [[ "$mode" == rollback ]]; then
  python3 prepare-gateway.py --check-message-store-on-flow
else
  python3 prepare-gateway.py --check-message-store-off-flow
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
  verify) printf '%s\n' '{"status":"passed","scope":"agent-message-store-off-verified","messageStore":false,"nativeTracing":"unchanged","servicesRestarted":false,"modelExecuted":false,"productionReady":false}' ;;
  rollback) printf '%s\n' '{"status":"passed","scope":"agent-message-store-off-rolled-back","messageStore":true,"nativeTracing":"unchanged","services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  *) printf '%s\n' '{"status":"passed","scope":"agent-message-store-off-applied","messageStore":false,"nativeTracing":"unchanged","services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
esac
