#!/usr/bin/env bash
# Switch the stored Gateway flow between the reviewed 4-call and 8-call builds and
# restart only the Gateway to pin it. No model invocation, request submission or
# cancellation, API key rotation, Langflow restart or MCP/Tunnel change.
#
# A switch is resumable: prepare-gateway.py writes .local/.gateway-flow-change before
# Langflow can change and this helper clears it only after every check passes. While
# it exists, or whenever the Gateway answers FLOW_CHANGED, the Gateway may refuse work
# or be stopped, so the rerun uses the local resume gate (the Gateway's own SQLite
# counts plus live MCP scopes) instead of the public, flow-verifying readiness check.
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
failed() {
  echo "AGENT_CALL_BUDGET_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2
  # Hint only when an unfinished switch is actually recorded.
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

if [[ "$mode" == verify ]]; then
  if [[ "$pending" == 1 ]]; then
    echo '이전 적용이 끝나지 않았습니다. 같은 적용 명령 또는 --rollback을 다시 실행해 마무리해 주세요.' >&2
    exit 1
  fi
else
  # This read-only observation is not an atomic maintenance lock: run it while
  # new web submissions are paused or quiet.
  phase=idle_check
  if python3 check-agent-queue-idle.py; then
    :
  else
    result=$?
    if [[ "$result" == 75 ]]; then
      echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 직접 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
      exit 75
    fi
    if [[ "$result" == 76 ]]; then
      # Stored flow and running pin differ (for example an interrupted earlier switch).
      echo 'Gateway가 흐름 불일치(FLOW_CHANGED)로 새 요청을 거부하고 있습니다. Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
    elif [[ "$pending" == 1 ]]; then
      echo '이전 흐름 변경이 끝나지 않았습니다. 공개 준비 확인 대신 Gateway 내부 작업 수와 MCP 실행 상태를 확인하고 이어서 진행합니다.' >&2
    else
      echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
      exit "$result"
    fi
    phase=resume_idle_check
    if python3 check-agent-queue-idle.py --resume; then
      :
    else
      result=$?
      if [[ "$result" == 75 ]]; then
        echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
      else
        echo 'Gateway 내부 작업 수 또는 MCP 실행 상태를 확인하지 못했습니다. 원인을 확인한 뒤 같은 명령을 다시 실행해 주세요. 이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다.' >&2
      fi
      exit "$result"
    fi
  fi
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
if [[ "$mode" != verify ]]; then
  phase=complete
  python3 prepare-gateway.py --complete-flow-change
fi
case "$mode" in
  verify) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-verified","modelCallLimit":8,"servicesRestarted":false,"modelExecuted":false,"productionReady":false}' ;;
  rollback) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-rolled-back","modelCallLimit":4,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
  *) printf '%s\n' '{"status":"passed","scope":"agent-call-budget-applied","modelCallLimit":8,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}' ;;
esac
