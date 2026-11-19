#!/usr/bin/env bash
# Update the MCP image only. No model invocation or changes to credentials/flows.
set +x
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml
  -f compose.gateway.yaml -f compose.tunnel.yaml)
phase=config
trap 'echo "CANDIDATE_RECOVERY_APPLY_FAILED: phase=$phase; modelExecuted=false; no credential values displayed." >&2' ERR
"${compose[@]}" config --quiet

# Build while the existing service remains available. Stop before recreation if
# a request is active; this observation is not an atomic maintenance lock.
phase=build
"${compose[@]}" build jeju-mcp
phase=idle_check
if "${compose[@]}" exec -T jeju-mcp node --input-type=module - <<'JS'
// CANDIDATE_RECOVERY_IDLE_CHECK: inspect live scopes; never register a test scope.
try {
  const token = process.env.GATEWAY_CONTROL_TOKEN;
  if (typeof token !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(token)) throw new Error();
  const response = await fetch('http://127.0.0.1:8788/gateway/readyz', {
    headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000),
  });
  const ready = await response.json();
  if (!response.ok || ready.ready !== true || ready.dataMode !== 'app-export'
    || ready.catalogSchemaVersion !== '2.0' || ready.placeCount !== 981
    || ready.readOnly !== true || ready.productionReady !== false
    || !Number.isSafeInteger(ready.activeScopes) || ready.activeScopes < 0) throw new Error();
  if (ready.activeScopes !== 0) {
    console.log(JSON.stringify({ status: 'deferred', code: 'ACTIVE_REQUEST_PRESENT',
      restarted: false, modelExecuted: false, productionReady: false }));
    process.exitCode = 75;
  } else {
    console.log(JSON.stringify({ status: 'passed', scope: 'idle-before-restart',
      activeScopes: 0, modelExecuted: false, productionReady: false }));
  }
} catch {
  console.error('CANDIDATE_RECOVERY_IDLE_CHECK_FAILED: no service was restarted; no response or secret values displayed.');
  process.exitCode = 1;
}
JS
then
  :
else
  result=$?
  if [[ "$result" == 75 ]]; then
    echo '현재 추천 요청이 실행 중입니다. 완료된 뒤 이 명령을 직접 다시 실행해 주세요. 컨테이너는 재시작하지 않았습니다.' >&2
  else
    echo 'MCP 요청 상태를 확인하지 못해 적용을 중단했습니다. 컨테이너는 재시작하지 않았습니다.' >&2
  fi
  exit "$result"
fi

phase=recreate_mcp
"${compose[@]}" up -d --no-deps --wait --wait-timeout 90 jeju-mcp
phase=offline_runtime_check
"${compose[@]}" exec -T jeju-mcp node --input-type=module - < tests/check-candidate-recovery.mjs
phase=public_readiness
python3 tunnel/check.py --scope public
printf '%s\n' '{"status":"passed","scope":"candidate-recovery-applied","service":"jeju-mcp","modelExecuted":false,"productionReady":false}'
