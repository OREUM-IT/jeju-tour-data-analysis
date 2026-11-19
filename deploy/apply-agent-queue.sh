#!/usr/bin/env bash
# Update only MCP + Gateway. No request submission, cancellation or credential changes.
set +x
set -euo pipefail
mode=apply
if [[ $# -eq 1 && "$1" == '--verify-only' ]]; then
  mode=verify
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash deploy/apply-agent-queue.sh [--verify-only]' >&2
  exit 2
fi
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
trap 'echo "AGENT_QUEUE_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2' ERR
"${compose[@]}" config --quiet

if [[ "$mode" == apply ]]; then
  # Finish both builds before any recreation. This read-only observation is not an
  # atomic maintenance lock: run while new web submissions are paused by the operator.
  phase=build
  "${compose[@]}" build jeju-mcp agent-gateway
  phase=idle_check
  if python3 check-agent-queue-idle.py; then
    :
  else
    result=$?
    if [[ "$result" == 75 ]]; then
      echo '대기·실행 중인 요청 또는 불확실 실행 보호 시간이 남아 있습니다. 모두 끝난 뒤 직접 다시 실행해 주세요. 서비스를 재시작하지 않았습니다.' >&2
    else
      echo 'MCP와 Gateway의 대기·실행 상태를 확인하지 못해 중단했습니다. 서비스를 재시작하지 않았습니다.' >&2
    fi
    exit "$result"
  fi
  phase=recreate_services
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 120 jeju-mcp agent-gateway
fi
phase=candidate_offline_check
"${compose[@]}" exec -T jeju-mcp node --input-type=module - < tests/check-candidate-recovery.mjs
phase=queue_offline_check
# Tests are deliberately excluded from the image. Stream each host test into the
# installed modules, sequentially. They use temporary SQLite and mock work only;
# they never open GATEWAY_STATE_PATH, call a model or inspect production answers.
for test_file in state jobs; do
  "${compose[@]}" exec -T --workdir /app agent-gateway node --input-type=module - \
    < "../services/agent-gateway/${test_file}.test.mjs"
done
phase=public_readiness
python3 tunnel/check.py --scope public
phase=queue_readiness
python3 check-agent-queue-idle.py --queue-ready
if [[ "$mode" == verify ]]; then
  printf '%s\n' '{"status":"passed","scope":"agent-queue-verified","servicesRestarted":false,"modelExecuted":false,"productionReady":false}'
else
  printf '%s\n' '{"status":"passed","scope":"agent-queue-applied","services":["jeju-mcp","agent-gateway"],"modelExecuted":false,"productionReady":false}'
fi
