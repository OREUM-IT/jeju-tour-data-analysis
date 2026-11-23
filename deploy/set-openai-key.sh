#!/usr/bin/env bash
# Register the OpenAI API key for the rule-action Gateway flow as a Langflow
# Credential. The key is read with a hidden prompt and streamed over stdin; it is
# never an argument, environment value, file or log line. No model is called.
set +x
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-oreumit-bootstrap}"
compose=(docker compose -p "$COMPOSE_PROJECT_NAME"
  -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml)
"${compose[@]}" config --quiet
if [[ ! -t 0 ]]; then
  echo '터미널에서 직접 실행해 주세요. 키는 화면에 표시되지 않는 입력으로만 받습니다.' >&2
  exit 2
fi
IFS= read -r -s -p 'OpenAI API 키(sk-로 시작, 입력은 표시되지 않음): ' key
echo >&2
# The reviewed host script is passed as code, never through a stale bind mount;
# printf is a shell builtin, so the key does not appear in any process arguments.
script="$(cat langflow/set-openai.py)"
status=0
printf '%s' "$key" | "${compose[@]}" exec -T langflow python -c "$script" || status=$?
unset key
exit "$status"
