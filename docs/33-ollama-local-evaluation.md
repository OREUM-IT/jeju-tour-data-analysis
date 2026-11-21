# 33. 로컬 Ollama 모델 96문항 비교 평가 (Windows PC)

기준일: 2026-11-21 KST. **실행 준비와 가짜 모델 종단 검사까지 완료했다. 실제 Ollama 모델 실행과 결과는 아직 없다.** 사용자가 Ollama 활용 2단계(같은 96문항으로 Gemini와 비교)를 요청했고, 실행 장비로 사용자 PC를 선택했다.

## 1. 목적과 범위

[28 문서](28-agent-v2-evaluation.md)의 Gemini v2 평가와 같은 템플릿 96문항·같은 정답으로 로컬 Ollama 모델(`qwen3:4b`)의 품질을 비교한다. 보는 항목은 한국어 구조화 요청 처리, MCP 도구 호출, 허용 밖 장소 ID, 최종 검증 통과, 확인 안내다.

- 사용자 PC는 Windows 11, Intel Core Ultra 9 185H, 메모리 32GB이며 **NVIDIA GPU가 없다**(사용자 제공). Ollama는 CPU로 실행한다.
- 같은 모델·양자화라면 답의 품질은 장비와 무관하지만 **속도는 운영 장비를 대표하지 않는다.** 지연은 참고값으로만 기록한다.
- 운영 Gateway·AWS 흐름·Vercel 앱은 바꾸지 않는다. `productionReady:false`를 유지한다.

## 2. Gemini v2 평가와 같은 것과 다른 것

| 항목 | Gemini v2 ([28 문서](28-agent-v2-evaluation.md)) | Ollama 평가 |
|---|---|---|
| 문항·정답 | 템플릿 96문항, 정답 `b9821945…` | 같음 |
| 명세·페르소나·테마 매핑 | `508c71ec…`·`5255d010…`·`eaf3e91a…` | 같음 |
| 채점 계획 | `d357ff6f…` | `8cea47bf…` |
| 카탈로그 | `adbbe3fd…` | `fa647bf5…` |
| 흐름 | `jeju-tour-agent-vertex-v2` (`020e2b0b…`) | `jeju-tour-agent-ollama-v2` (`7857ef60…`) |
| 모델 | Vertex `gemini-3.8-flash` | Ollama `qwen3:4b`, 생각하기(thinking) 끔 |
| Agent 제한 | 90초, 모델 4회 | **900초**, 모델 4회 (`cpu-evaluation` 프로필) |
| 모델 요청 제한 | 60초, 재시도 1회 | 600초, 재시도 없음 |
| 수집 제한 | 120초 | 960초 (`--local-model-timeout`) |

- **계획·카탈로그 해시:** 카탈로그 해시만 다르고, 그 때문에 계획 해시가 달라진다. 카탈로그 차이는 앱 덤프 시각(`source.appGeneratedAt`)으로 추정한다. 새 계획의 카탈로그 해시만 원래 값으로 바꾸면 계획 해시가 정확히 `d357ff6f…`가 되는 것을 메모리에서 확인했다. 즉 96개 요청과 정답은 원 평가와 같다.
- **흐름:** Agent의 모델 선택, 모델 생성 코드(`_get_llm`), Vertex 자격 증명 참조만 바꿨다. `cpu-evaluation` 프로필은 Agent 시간 상수도 바꾼다. 나머지 노드·프롬프트·MCP 도구·최종 검증·연결선은 그대로다.
- **시간 제한:** CPU 속도 때문에 시간 제한에 걸려 품질과 무관한 실패가 나지 않도록 늘렸다. 모델 호출 4회 제한은 그대로 둔다.
- **샘플링:** Gemini 흐름처럼 온도 등 샘플링 값은 모델 기본값을 쓴다. 생각하기 출력이 최종 JSON 앞에 붙으면 최종 검증이 막으므로 `reasoning=False`(Ollama `think:false`)로 끈다.

## 3. 구현

- `services/langflow/build_ollama_flow.py`: 고정된 v2 흐름에서 Ollama 평가 흐름을 만드는 순수 변환기다.
  - 입력 흐름 해시와 `_get_llm` 원문 해시가 고정값과 다르면 거부한다.
  - 선택 모델이 검토한 태그·공급자와 다르면 실행 시 `OLLAMA_MODEL_SELECTION_INVALID`로 막는다.
  - Ollama 주소는 `http://127.0.0.1:11434`로 고정한다. `--check`로 커밋된 산출물과 재생성 결과가 같은지 확인한다.
- `services/langflow/jeju-tour-agent-ollama-v2.flow.json`: `qwen3:4b`·`cpu-evaluation` 산출물이다. `scripts/check_repository.py`가 해시와 재생성 일치, 자격 증명 부재를 검사한다.
- `services/jeju-mcp/scripts/collect-baseline-responses.mjs`의 `--local-model-timeout`:
  - `--system langflow --request-mode langflow-v2`와 `http://127.0.0.1:<포트>` 엔드포인트에서만 허용한다. `--timeout-ms` 상한을 1,200,000ms로 올린다.
  - Node 기본 `fetch`는 응답 헤더를 300초까지만 기다리므로, 이 옵션을 켜면 loopback 전용 HTTP 전송을 쓴다.
  - 설정은 계측 파일에 기록되고 재개 때 바뀌면 거부한다. 옵션이 없으면 기존 동작과 같다.
- `services/langflow/local_ollama_bootstrap.py`: PC 한 대에서 Langflow·MCP·Ollama를 확인하고 흐름을 가져온다. [AWS bootstrap](../deploy/langflow/bootstrap.py)과 같은 원칙을 따르며 하는 일은 다음과 같다.
  - 익명 접근과 자동 로그인을 거부하고, Langflow 1.13.3을 요구한다.
  - MCP v2 준비 상태(981곳)를 확인하고, Ollama 태그와 도구 지원(`tools` capability)을 확인한다.
  - `JEJU_MCP_TOKEN` Credential을 만들고 `jeju-mcp-v2` 서버를 등록한다. 이미 있으면 그대로 둔다.
  - `--replace-mcp-credential`을 주면, MCP가 직접 받아 준 `MCP_SERVICE_TOKEN`으로 저장된 `JEJU_MCP_TOKEN` 값을 바꾼다(출력 `mcpCredential: replaced`). 저장된 토큰을 고치는 **유일한 방법**이다.
    - Langflow 화면에서 값을 입력하거나 붙여 넣지 않는다. 끝에 공백이 붙으면 아래 MCP 확인은 통과한다(MCPTools가 헤더 공백을 지운다). 그러나 요청 준비 단계는 정확한 값만 받으므로 모든 문항이 실패한다.
    - 실제 Langflow 1.13.3에서 재현했다. 저장 토큰 끝에 공백을 붙이면 설정 스크립트는 `passed`였지만 1문항이 `http_error`로 실패했다. 이 옵션으로 다시 실행한 뒤에는 같은 문항이 정상 응답했다.
  - **Langflow 쪽 MCP 확인:** Langflow는 Credential 값과 등록 헤더 값을 가려서 돌려주므로 앞 단계는 항목이 있는지만 안다. 이 확인이 없으면 오래된 토큰이 남아 있어도 `passed`가 나오고, 모든 문항이 요청 준비 단계(`REQUEST_PREPARATION_FAILED`)에서 실패한다.
    - 흐름을 가져오기 전에 Langflow의 MCPTools 구성 요소로 도구 목록을 두 번 받는다. 한 번은 저장된 Credential을 헤더(`Bearer {{JEJU_MCP_TOKEN}}`)로 강제하고, 한 번은 흐름 실행 때처럼 등록된 헤더만 쓴다.
    - 두 번 모두 `constraints.check`·`places.get`·`places.search` 3개가 정확히 나와야 하며, 통과하면 출력에 `mcpProbe: passed`가 붙는다. 모델과 흐름은 실행하지 않고 아무것도 저장하지 않는다.
    - 실패하면 흐름을 가져오지 않고 API 키도 만들지 않는다. 기존 Credential·등록·흐름도 바꾸지 않으므로 원인을 고친 뒤 다시 실행한다. 실패 코드는 다음과 같다.
      - `LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED`: 저장된 `JEJU_MCP_TOKEN`으로 목록을 받지 못했다. 저장된 토큰이 지금 MCP 토큰과 다르거나(예: `.env.local`을 다시 만든 경우), `langflow.env`에 `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`이 없거나, 프록시가 있는 PC에서 `NO_PROXY`가 빠져 Langflow가 MCP에 닿지 못한 경우다. 토큰이 바뀐 경우라면 같은 명령에 `--replace-mcp-credential`을 붙여 다시 실행한다.
      - `LANGFLOW_MCP_REGISTRATION_PROBE_FAILED`: 저장된 토큰은 맞지만 등록된 헤더가 다른 토큰이나 변수를 쓴다.
      - `MCP_TOOL_SET_MISMATCH`: 받은 도구가 3개와 다르다.
      - `LANGFLOW_MCP_PROBE_FAILED`: 확인 요청 자체가 거부되거나 응답 형식이 다르다.
  - 흐름을 가져온 뒤 저장 내용이 파일과 같은지 비교한다.
  - API 키는 `--write-api-key`로 지정한 새 파일에만 쓰고 출력하지 않는다.

## 4. PC 준비 (한 번)

필요한 것은 다음과 같다.
- **Python 3.13:** 3.10은 안 된다. 흐름 코드가 3.11부터 있는 `asyncio.timeout`을 쓴다.
- Node 24, Git, Ollama
- 디스크 약 6GB: Langflow 약 2GB, 모델 2.5GB

아래 명령은 PowerShell에서 저장소 루트(예: `C:\work\oreumit\jeju-tour-data-analysis`) 기준이다.

1. **모델 확인:** `ollama pull qwen3:4b`를 실행한 뒤 `ollama show qwen3:4b`의 Capabilities에 `tools`가 있어야 한다.
2. **Langflow 설치:** `py -3.13 -m venv .langflow-venv`로 가상환경을 만들고, `.\.langflow-venv\Scripts\python.exe -m pip install langflow==1.13.3`으로 설치한다.
3. **MCP 의존성:** `cd services\jeju-mcp`, `npm ci --ignore-scripts --no-audit --no-fund`, `cd ..\..`
4. **평가 자료:** 작업자가 따로 전달한 파일 3개를 `services\jeju-mcp\.local\`에 둔다. Git에는 넣지 않는다.
   - `agent-catalog-b0602a1.json`
   - `evaluation-set-template-b0602a1.json`
   - `baseline-plan-template-b0602a1.json`
5. **MCP 토큰:** `cd services\jeju-mcp`, `node scripts/configure-local.mjs`, `cd ..\..`
   - `.env.local`을 만들고 토큰은 출력하지 않는다. 파일의 포트 8788은 실행 때 환경 변수 8789로 덮어쓴다.
6. **Langflow 설정:** `Copy-Item services\langflow\langflow.env.example services\langflow\langflow.env`로 복사한다.
   - `LANGFLOW_SUPERUSER_PASSWORD`를 긴 무작위 값으로 바꾼다.
   - `LANGFLOW_AUTO_LOGIN=false`, `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`을 유지한다.
   - `NO_PROXY=127.0.0.1,localhost`가 반드시 있어야 한다. 없으면 시스템이나 환경 변수에 프록시가 설정된 PC에서 Langflow가 Ollama·MCP로 보내는 loopback 요청도 프록시를 거친다. 예시가 바뀌기 전에 만든 `langflow.env`에도 이 줄을 더한다.

## 5. 실행

창을 세 개 연다. Ollama는 설치 후 트레이에서 실행 중이어야 한다.

1. **창 1, MCP v2:**
   ```
   cd services\jeju-mcp; $env:HOST="127.0.0.1"; $env:PORT="8789"; $env:MCP_CATALOG_PATH="$PWD\.local\agent-catalog-b0602a1.json"; node --env-file=.env.local src\server.mjs
   ```
2. **창 2, Langflow:**
   ```
   .\.langflow-venv\Scripts\langflow.exe run --backend-only --env-file services\langflow\langflow.env
   ```
3. **창 3, 설정과 흐름 가져오기.** 비밀값을 화면이나 기록에 남기지 않도록 파일에서 읽는다.
   ```
   Get-Content services\langflow\langflow.env | ForEach-Object { if ($_ -match '^(LANGFLOW_SUPERUSER|LANGFLOW_SUPERUSER_PASSWORD)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] } }
   Get-Content services\jeju-mcp\.env.local | ForEach-Object { if ($_ -match '^MCP_SERVICE_TOKEN=(.*)$') { $env:MCP_SERVICE_TOKEN = $Matches[1] } }
   .\.langflow-venv\Scripts\python.exe services\langflow\local_ollama_bootstrap.py --write-api-key services\jeju-mcp\.local\langflow-api-key.env
   Remove-Item Env:LANGFLOW_SUPERUSER_PASSWORD, Env:MCP_SERVICE_TOKEN
   ```
   출력의 `status`와 `mcpProbe`가 `passed`이면 `flowId`를 적어 둔다. 실패 코드의 뜻은 3절에 있다.
   - `LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED`가 나오고 `.env.local`을 다시 만든 적이 있다면 같은 명령 끝에 `--replace-mcp-credential`을 붙여 다시 실행한다. 이미 API 키 파일이 있으면 `--write-api-key` 부분은 뺀다.
   - 토큰은 Langflow 화면에서 고치지 않는다(3절).
4. **10문항 시범.** `services\jeju-mcp`에서 실행한다.
   ```
   Get-Content .local\langflow-api-key.env | ForEach-Object { if ($_ -match '^LANGFLOW_API_KEY=(.*)$') { $env:LANGFLOW_API_KEY = $Matches[1] } }
   node scripts/collect-baseline-responses.mjs --execute --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-template-b0602a1.json --plan .local/baseline-plan-template-b0602a1.json --system langflow --request-mode langflow-v2 --model "qwen3:4b" --prompt-version "jeju-v2-ollama-7857ef60" --tool-version "jeju-mcp-v2-agent-tools-v2.0" --endpoint "http://127.0.0.1:7860/api/v1/run/<flowId>?stream=false" --output-index 0 --component-id ChatOutput-jejuV2 --max-cases 10 --local-model-timeout --timeout-ms 960000 --output .local/ollama-capture-1.json --telemetry .local/ollama-telemetry-1.json
   ```
5. **이어서 실행.** 수집기는 첫 실패에서 멈추고 자동으로 다시 부르지 않는다. 같은 명령에 아래를 더해 아직 실행하지 않은 문항만 이어간다.
   - `--resume-capture .local/ollama-capture-1.json --resume-telemetry .local/ollama-telemetry-1.json`
   - 새 출력 경로(`-2`, `-3` …)와 `--max-cases <추가할 수>`
   - `--timeout-ms`와 `--local-model-timeout`은 처음과 같게 유지한다.
6. **채점.** 마지막 수집 파일로 실행한다. `--report`는 공개 가능한 집계이므로 `.local` 밖에 둔다.
   ```
   node scripts/evaluate-baselines.mjs import --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-template-b0602a1.json --plan .local/baseline-plan-template-b0602a1.json --capture .local/<마지막 capture>.json --output .local/ollama-run.json
   node scripts/evaluate-baselines.mjs score --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-template-b0602a1.json --plan .local/baseline-plan-template-b0602a1.json --run .local/ollama-run.json --details .local/ollama-scores.json --report ..\..\reports\baseline-ollama-qwen3-4b-cpu-<날짜>.json
   ```

**첫 문항이 모델이 아니라 설정 때문에 실패하면 이어서 실행하지 않는다.** 예: 요청 준비의 `REQUEST_PREPARATION_FAILED`, API 키 거부, 꺼진 서버. 이어서 실행하면 모델과 무관한 그 실패가 96문항 분모에 남는다. 멈추고 설정을 고친 뒤 설정 스크립트를 다시 실행해 `passed`를 확인하고(API 키 파일이 이미 있으면 `--write-api-key` 없이), `--resume-*` 없이 새 파일 이름(예: `ollama-capture-r2-1.json`, `ollama-telemetry-r2-1.json`)으로 처음부터 새로 수집한다. 중단된 수집·계측 파일은 지우거나 덮어쓰지 않고 `.local`에 두며, 결과 보고에 그 파일 이름과 중단 이유를 적는다.

흐름을 Langflow 화면에서 열어 저장하면 저장 내용이 파일과 달라진다. 그러면 설정 스크립트가 `EXISTING_FLOW_DIFFERS`로 멈추고, 선택이 바뀌면 실행 시 `OLLAMA_MODEL_SELECTION_INVALID`로 막힌다. 흐름은 API로만 가져와 실행한다.

## 6. 결과 해석

- **비교 기준:** [28 문서](28-agent-v2-evaluation.md)의 Gemini v2 결과다.
  - 정상 응답 73/96, 실패 23
  - 동작 정답 67/96
  - 허용 밖 ID 7/295
  - 필수 조건 오판 0/17
  - 확인 안내 17/31
  - 정상 응답 지연 p50 11.886초, p95 50.100초
- **같이 기록할 것:** 실패도 분모에 포함한다. 실패 분류(최종 검증 차단, 시간 초과, 전송 오류)를 같이 기록한다.
- **해석 한계:** CPU 지연과 `cpu-evaluation`의 시간 제한 차이를 고려해 모델 우열이나 운영 가능 여부로 확대하지 않는다. 28 문서의 `contextOnly` 한계와 네 확인 주제 제한도 그대로 적용된다.
- **공개 범위:** 질의·장소 ID·원문 응답이 든 수집·계측·문항별 점수는 `.local`에만 둔다. 공개 보고서에는 집계만 넣는다.

## 7. 이 준비 단계의 검증 기록

모두 클라우드 작업 환경에서 실행했다. 실제 모델은 실행하지 않았다.

| 검사 | 결과 |
|---|---|
| `python3 scripts/check_repository.py` | 통과(새 Ollama 흐름 검사 포함) |
| `python3 -m unittest discover -s services/langflow/tests` | 통과. httpx가 없는 CI형 환경에서는 HTTP 테스트를 건너뛴다. lfx 1.13.3 가상환경에서는 건너뜀 없이 통과 |
| `npm test` (`services/jeju-mcp`) | 226/226 통과 |
| 실제 lfx 1.13.3 생성 확인 | `ChatOllama` 생성 확인(`qwen3:4b`, `127.0.0.1:11434`, `num_ctx` 16384, `num_predict` 2048, `keep_alive` 30m, `reasoning=False`, 요청 제한 600초). 네트워크 연결 0회 |
| **가짜 Ollama 종단 검사** | 실제 Langflow 1.13.3, MCP v2(981곳), 가짜 Ollama로 아래를 모두 실행 |

종단 검사 구성은 다음과 같다. 가짜 Ollama는 같은 HTTP API로 검색 → 조건 확인 → 최종 JSON 순서를 따르는 결정적 서버이고, 저장소에 넣지 않았다.
- 경로: 설정 스크립트 → 수집기(`--local-model-timeout --timeout-ms 960000`) → 요청 준비 → Agent 도구 호출 → 최종 검증 → 가져오기·채점
- 결과: 13문항 모두 정상 응답이었다(점검용 흐름 10문항, 정식 흐름 3문항). 모델 요청에는 `think:false`, `num_ctx` 16384, `num_predict` 2048, `keep_alive` 30m이 실렸다.
- 독립 검토 반영 후 실제 Langflow 1.13.3에서 다시 확인했다.
  - 설정 스크립트의 Langflow 쪽 MCP 확인이 정상 상태에서 `mcpProbe: passed`를 냈다. 저장 토큰이 MCP 토큰과 다른 경우는 `LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED`로 멈췄다(검토자의 별도 인스턴스 실행).
  - 저장 토큰 끝에 공백을 붙이면 설정 스크립트는 `passed`였지만 1문항이 `http_error`로 실패했다. `--replace-mcp-credential` 실행 후(`replaced`) 같은 1문항이 정상 응답했다.
- 이 결과는 연결 확인일 뿐이며 실제 모델의 품질·형식 준수·시간을 뜻하지 않는다.

남은 것은 다음과 같다.
- 사용자 PC에서의 실제 `qwen3:4b` 10문항 시범과 96문항 실행
- Windows에서의 명령·경로·loopback 전송 동작 확인
- Ollama용 런타임 감사(`audit_v2_runtime.py`는 Google 공급자를 가정하므로 별도 변형 필요)
