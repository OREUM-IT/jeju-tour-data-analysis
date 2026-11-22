# 33. 로컬 Ollama 모델 96문항 비교 평가 (Windows PC)

기준일: 2026-11-22 KST. **실행 준비, 가짜 모델 종단 검사, Windows 점검 반영까지 완료했다. 실제 Ollama 모델 실행과 결과는 아직 없다.** 사용자가 Ollama 활용 2단계(같은 96문항으로 Gemini와 비교)를 요청했고, 실행 장비로 사용자 PC를 선택했다.

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
- `services/jeju-mcp/scripts/run-local-ollama-collection.mjs`: 이 평가 전용 실행 도구다(5절 4~6단계).
  - 수집기가 실패한 문항마다 멈추므로, 수집기를 같은 재개 사슬로 반복해서 불러 `--target` 문항 수까지 진행한다. 실패 문항을 다시 보내지 않고 모델·프롬프트·흐름·시간 제한도 바꾸지 않는다.
  - [28 문서](28-agent-v2-evaluation.md)의 비공개 조정기와 원칙이 같다. 매 구간 전에 세 서비스의 응답과, 저장된 흐름이 커밋 파일과 같은지 확인한다. Agent 오류가 3번 연속이면 멈춘다.
  - 28 문서와 달리 Langflow 활성 작업 수는 확인하지 않는다(SQLite를 직접 조회해야 한다). 대신 960초 시간 초과가 나면 바로 멈춘다.
  - 끝점은 흐름 이름으로 찾고 API 키는 파일에서 읽는다. 그래서 PowerShell 자리 표시나 따옴표 때문에 생기는 문제가 없다.
- 수집기 변경:
  - `stopSignal`: 실행 도구에서 Ctrl+C를 한 번 누르면 쓰인다. 진행 중인 요청을 끊지 않고 끝까지 기록한 뒤 다음 문항을 시작하지 않는다(`operator_stop`). 기존 `signal`과 CLI 동작은 그대로다.
  - Windows에서 체크포인트 교체(`rename`)가 `EPERM`·`EACCES`·`EBUSY`로 실패하면, 대상 파일이 그대로인지 다시 확인하며 약 35초까지 재시도한다. 다른 OS나 다른 오류는 이전처럼 바로 실패한다.

## 4. PC 준비 (한 번)

필요한 것은 다음과 같다.
- **Python 3.13:** 3.10은 안 된다. 흐름 코드가 3.11부터 있는 `asyncio.timeout`을 쓴다.
- **Node 24.2 이상**(사용자 PC 24.21.0), Git, Ollama
- 디스크 약 6GB: Langflow 약 2GB, 모델 2.5GB

**창과 경로 규칙.** 2026-11-22 Windows 점검(7절)에서 확인한 내용이다.
- 관리자 권한이 아닌 **일반 Windows PowerShell**을 쓴다. 관리자 창은 `C:\Windows\System32`에서 시작하므로 상대 경로 명령이 엉뚱한 곳에 파일을 만든다.
- 저장소 경로는 `C:\work\jeju-tour-data-analysis`처럼 **36자 이하**로 하고, OneDrive와 동기화되지 않는 곳에 둔다.
  - Windows 긴 경로 지원(`LongPathsEnabled`)이 꺼져 있고 경로가 36자를 넘으면 Langflow 설치가 실패한다. langwatch 패키지에 `\.langflow-venv\Lib\site-packages\` 아래 189자인 파일이 있어 전체 경로가 259자를 넘기 때문이다. 위 예시 경로(31자)에서는 가장 긴 경로가 254자다.
  - 바탕 화면이나 문서 폴더가 OneDrive와 동기화되면 `.local`의 비밀값과 평가 원문이 함께 올라간다.
- 각 블록은 `Set-Location`으로 절대 경로로 이동한 뒤 실행한다.
- Windows PowerShell 5.1에서는 `npm` 대신 `npm.cmd`를 쓴다. 기본 실행 정책(Restricted)이 `npm.ps1`을 막는다.

1. **저장소 받기:**
   ```
   New-Item -ItemType Directory -Force C:\work | Out-Null
   git clone https://github.com/OREUM-IT/jeju-tour-data-analysis.git C:\work\jeju-tour-data-analysis
   Set-Location C:\work\jeju-tour-data-analysis
   ```
2. **모델 확인:** `ollama pull qwen3:4b`를 실행한 뒤 `ollama show qwen3:4b`의 Capabilities에 `tools`가 있는지 확인한다.
3. **Langflow 설치:** 저장소 루트에서 실행한다.
   ```
   py -3.13 -m venv .langflow-venv
   .\.langflow-venv\Scripts\python.exe -V
   .\.langflow-venv\Scripts\python.exe -m pip install langflow==1.13.3
   ```
   - 둘째 줄이 `Python 3.13.x`를 출력해야 한다. 다른 버전이면 `.langflow-venv`를 지우고, `py -0p` 목록에 3.13이 있는지 확인한 뒤 다시 만든다.
   - pip가 긴 경로 안내와 함께 `No such file or directory ... langwatch`로 멈추면 관리자 PowerShell에서 `Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1 -Type DWord`를 실행한다. 그다음 일반 창을 새로 열고 같은 pip 명령을 다시 실행한다.
4. **MCP 의존성:**
   ```
   Set-Location C:\work\jeju-tour-data-analysis\services\jeju-mcp
   npm.cmd ci --ignore-scripts --no-audit --no-fund
   ```
5. **평가 자료:** 작업자가 따로 전달한 `jeju-ollama-eval-data-b0602a1.zip`을 `services\jeju-mcp\.local\`에 바로 풀고 해시를 확인한다. 탐색기의 "압축 풀기"는 폴더를 한 단계 더 만들므로 쓰지 않는다. 이 자료는 Git에 넣지 않는다.
   ```
   Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\jeju-ollama-eval-data-b0602a1.zip" -DestinationPath C:\work\jeju-tour-data-analysis\services\jeju-mcp\.local
   Set-Location C:\work\jeju-tour-data-analysis\services\jeju-mcp\.local
   Get-Content SHA256SUMS.txt | ForEach-Object { $h, $n = $_ -split '\s+', 2; if ((Get-FileHash -Algorithm SHA256 -LiteralPath $n).Hash -eq $h) { "OK    $n" } else { "FAIL  $n" } }
   ```
   다음 세 파일에 대해 `OK` 줄이 나와야 한다.
   - `agent-catalog-b0602a1.json`
   - `evaluation-set-template-b0602a1.json`
   - `baseline-plan-template-b0602a1.json`
6. **MCP 토큰:** `services\jeju-mcp`에서 `node scripts\configure-local.mjs`를 실행한다.
   - `.env.local`을 만들고 토큰은 출력하지 않는다. 파일의 포트 8788은 실행할 때 환경 변수 8789로 덮어쓴다.
   - 출력의 "private"는 POSIX 기준이다. Windows는 파일 권한 0600을 적용하지 않고 폴더 권한을 물려받는다. 여러 사람이 쓰는 PC라면 `icacls <파일> /inheritance:r /grant:r "${env:USERNAME}:(M)"`로 좁힌다. `langflow.env`와 API 키 파일도 같다.
7. **Langflow 설정:** 저장소 루트에서 실행하며, **Langflow를 처음 켜기 전에** 끝낸다.
   ```
   Set-Location C:\work\jeju-tour-data-analysis
   if (-not (Test-Path services\langflow\langflow.env)) { Copy-Item services\langflow\langflow.env.example services\langflow\langflow.env } else { 'langflow.env already exists: not copied' }
   $f = (Resolve-Path services\langflow\langflow.env).Path; $b = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $pw = -join ($b | ForEach-Object { $_.ToString('x2') }); [IO.File]::WriteAllText($f, ([IO.File]::ReadAllText($f) -replace '(?m)^LANGFLOW_SUPERUSER_PASSWORD=[^\r\n]*', "LANGFLOW_SUPERUSER_PASSWORD=$pw")); Remove-Variable b, pw
   Select-String -Path services\langflow\langflow.env -Pattern '^LANGFLOW_SUPERUSER_PASSWORD=[0-9a-f]{48}$' -Quiet
   ```
   - 마지막 줄이 `True`를 출력해야 한다. 비밀번호는 화면에 나오지 않는다.
   - 비밀번호는 영문·숫자만 쓰고 따옴표·공백·`#`·`$`는 넣지 않는다. Langflow는 python-dotenv로 값을 읽으면서 따옴표·주석·공백을 걷어 낸다. 반면 창 3의 읽기 명령은 `=` 뒤를 그대로 쓴다. 두 값이 달라지면 `LOGIN_FAILED`가 된다.
   - 이 파일은 Windows PowerShell 5.1의 `Set-Content`·`Add-Content`·`Out-File`·`>`로 쓰지 않는다. 이 명령들은 cp949나 UTF-16으로 저장하고, 그러면 Langflow가 `UnicodeDecodeError`로 시작하지 못한다. 손으로 고칠 때는 메모장(UTF-8 저장)을 쓴다.
   - **Langflow를 한 번 켠 뒤에는 비밀번호를 바꾸지 않는다.** Langflow 1.13.3은 이 값을 관리자 계정을 처음 만들 때만 쓰고, 그 뒤에는 저장된 비밀번호를 유지한다. 이때 창 2에는 `LANGFLOW_SUPERUSER_PASSWORD was not applied` 경고만 남는다. 비밀번호를 바꾸면 설정 스크립트가 `LOGIN_FAILED`로 멈추므로, 원래 값으로 되돌리거나 아래 초기화를 따른다.
   - `LANGFLOW_AUTO_LOGIN=false`, `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`, `NO_PROXY=127.0.0.1,localhost`를 유지한다.
     - `NO_PROXY`가 없으면 프록시가 설정된 PC에서 Langflow가 Ollama·MCP로 보내는 loopback 요청도 프록시를 거친다.
     - 예시가 바뀌기 전에 만든 `langflow.env`에도 이 줄을 더한다.
   - **초기화(원래 비밀번호를 잃었을 때):**
     1. 창 2를 닫는다.
     2. `Remove-Item .langflow-venv\Lib\site-packages\langflow\langflow.db*`로 이 평가용 Langflow DB를 지운다. 흐름·API 키·Credential도 함께 지워진다.
     3. Langflow를 다시 켜고, 설정 스크립트를 새 키 파일 이름(`--write-api-key services\jeju-mcp\.local\langflow-api-key-2.env`)으로 실행한다.
     4. 수집은 새 시리즈(`--series ollama-r2 --api-key-file langflow-api-key-2.env`)로 처음부터 한다. 흐름 ID가 바뀌었으므로 이전 파일에서는 이어서 실행할 수 없다.

## 5. 실행

**전원과 창 (긴 CPU 실행 전).**
- Langflow·MCP·수집기·Ollama는 Windows에 절전하지 말라고 요청하지 않는다. Windows는 CPU 사용량과 상관없이, 입력이 없는 시간이 지나면 절전한다.
- 절전하는 동안에도 시간 제한 시계는 흐른다. 그래서 진행 중이던 문항이 시간 초과로 기록되고 수집이 멈출 수 있다. 전원을 연결하고 덮개를 열어 둔 채 아래를 실행한다.
  ```
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```
  평가가 끝나면 설정 > 시스템 > 전원 및 배터리에서 원래 값으로 되돌린다.
- 96문항을 도는 동안에는 설정 > Windows 업데이트에서 업데이트를 일시 중지하고, Ollama의 업데이트 재시작 알림도 누르지 않는다.
- 창은 Windows 터미널(Windows 11 기본)에서 연다.
  - 예전 콘솔 창에서 빠른 편집 모드가 켜져 있으면, 창 안을 클릭하는 순간 그 창의 출력이 멈춘다. Langflow 창이면 응답까지 멈춘다.
  - 창 제목이 `선택`으로 시작하면 Esc를 누른다.
- 수집하는 동안에는 `.local`의 수집·계측 파일을 열지 않는다(엑셀, `Get-Content -Wait` 등). 파일을 열어 두면 체크포인트 교체가 막힐 수 있다. 수집기는 Windows에서 이런 공유 잠금을 약 35초까지 다시 시도한다.

창을 세 개 연다. Ollama는 설치 후 트레이에서 실행 중이어야 한다.

1. **창 1, MCP v2:**
   ```
   Set-Location C:\work\jeju-tour-data-analysis\services\jeju-mcp
   $env:HOST="127.0.0.1"; $env:PORT="8789"; $env:MCP_CATALOG_PATH="$PWD\.local\agent-catalog-b0602a1.json"
   node --env-file=.env.local src\server.mjs
   ```
   `Read-only MCP prototype listening on port 8789; dataMode=app-export; productionReady=false.`가 나와야 한다. `MCP startup failed...`가 나오면 4절 5단계의 자료 위치(`Test-Path .local\agent-catalog-b0602a1.json`)와 6단계의 `.env.local`을 확인한다.
2. **창 2, Langflow:**
   ```
   Set-Location C:\work\jeju-tour-data-analysis
   .\.langflow-venv\Scripts\langflow.exe run --backend-only --env-file services\langflow\langflow.env
   ```
   - Windows에서는 `[OK] Open Langflow ->` 안내가 서버가 시작되기 **전에** 나온다. 첫 실행은 DB 준비 때문에 몇 분 걸릴 수 있으니 `Uvicorn running on http://127.0.0.1:7860` 줄을 기다린다.
   - 안내에 나온 포트가 7860이 아니면 다른 프로그램이 이미 7860을 쓰고 있다는 뜻이다. 멈추고 그 프로그램을 끈다. 설정 스크립트와 실행 도구는 7860에 고정돼 있다.
   - `pip install langflow -U` 안내는 무시한다(1.13.3 고정).
3. **창 3, 설정과 흐름 가져오기.** 저장소 루트에서 아래 블록을 함께 실행한다. 비밀값은 화면이나 기록에 남지 않도록 파일에서 읽고, 끝나면 창에서 지운다.
   ```
   Set-Location C:\work\jeju-tour-data-analysis
   Get-Content services\langflow\langflow.env | ForEach-Object { if ($_ -match '^(LANGFLOW_SUPERUSER|LANGFLOW_SUPERUSER_PASSWORD)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] } }
   Get-Content services\jeju-mcp\.env.local | ForEach-Object { if ($_ -match '^MCP_SERVICE_TOKEN=(.*)$') { $env:MCP_SERVICE_TOKEN = $Matches[1] } }; Remove-Variable Matches -ErrorAction SilentlyContinue
   .\.langflow-venv\Scripts\python.exe services\langflow\local_ollama_bootstrap.py --write-api-key services\jeju-mcp\.local\langflow-api-key.env
   Remove-Item Env:LANGFLOW_SUPERUSER_PASSWORD, Env:MCP_SERVICE_TOKEN
   ```
   출력의 `status`와 `mcpProbe`가 `passed`여야 한다. 실패 코드의 뜻은 3절에 있다.
   - **다시 실행할 때**는 블록 전체를 저장소 루트에서 다시 실행하고, 설정 스크립트 줄만 아래처럼 고친다. 마지막 줄이 비밀값을 지우므로 설정 스크립트 줄만 다시 실행하면 `BOOTSTRAP_CREDENTIALS_INVALID`가 된다.
     - API 키 파일이 이미 있으면 `--write-api-key ...`를 뺀다. 그대로 두면 `API_KEY_FILE_EXISTS`가 나온다.
     - `LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED`가 나왔고 `.env.local`을 다시 만든 적이 있다면 `--replace-mcp-credential`을 붙인다. 토큰은 Langflow 화면에서 고치지 않는다(3절).
   - `LANGFLOW_UNAVAILABLE`: 창 2가 아직 준비 중이다. `Uvicorn running` 줄이 나온 뒤 다시 실행한다.
   - `LOGIN_FAILED`의 원인은 셋 중 하나다.
     - Langflow를 처음 켠 뒤 비밀번호를 바꿨다(4절 7단계).
     - 값에 따옴표·공백·`#`가 있다.
     - 1분 안에 여섯 번 넘게 실행했다. Langflow는 IP당 분당 로그인 5회만 허용하고, 그 이상은 비밀번호가 맞아도 429로 거부해 같은 코드로 보인다. 1분 기다린 뒤 다시 실행한다.
4. **실행 전 확인:** `services\jeju-mcp`에서 실행한다. 평가 요청은 보내지 않는다.
   ```
   Set-Location C:\work\jeju-tour-data-analysis\services\jeju-mcp
   node scripts\run-local-ollama-collection.mjs --check
   ```
   마지막 줄이 `"result":"checked"`이면 세 서비스가 응답하고, Langflow에 저장된 흐름이 커밋된 흐름 파일과 같다는 뜻이다.
5. **10문항 시범:**
   ```
   node scripts\run-local-ollama-collection.mjs --target 10
   ```
   - **실행 도구가 하는 일:** 실행 도구(`run-local-ollama-collection.mjs`)는 수집기(`collect-baseline-responses.mjs`)를 하나의 재개 사슬로 반복해서 부른다. 수집기가 실패한 문항마다 멈추고 스스로 다시 시작하지 않기 때문이다.
   - **넘기는 값:** 고정된 계약을 그대로 넘긴다. 자료 파일 3개, `qwen3:4b`, 프롬프트·도구 버전, `--local-model-timeout --timeout-ms 960000`, 출력 노드다. 끝점은 Langflow에서 흐름 이름으로 찾고, API 키는 `.local\langflow-api-key.env`에서 읽으며 출력하지 않는다.
   - **결과 파일:** `.local\ollama-capture-<n>.json`과 `.local\ollama-telemetry-<n>.json`이다. 구간마다 n이 1씩 늘고, 직전 쌍을 `--resume-*`로 넘긴다. 이전 파일은 바꾸거나 지우지 않는다.
   - **구간 전 확인:** 매 구간 전에 세 서비스의 응답(Ollama `/api/version`, MCP `/healthz`, Langflow `/health_check`)을 확인한다. Langflow에 저장된 흐름 데이터가 커밋된 흐름 파일과 같은지도 확인한다.
   - **실패 처리:** 실패한 문항은 다시 실행하지 않고 분모에 남긴다. 마지막 문항의 결과에 따라 다음처럼 진행한다.

     | 마지막 문항 결과 | 실행 도구 |
     |---|---|
     | `final_validation_blocked`, `final_validation_failed`, `invalid_validated_output` | 기록하고 다음 문항으로 계속 |
     | Agent가 낸 HTTP 500(`AGENT_EXECUTION_FAILED`·`AGENT_EXECUTION_TIMEOUT`) | 계속. 3번 연속이면 멈춤(28 문서와 같은 기준) |
     | 그 밖의 HTTP 상태나 HTTP 500(예: MCP가 꺼져 MCP Tools 구성 요소가 실패), 전송 실패, 960초 시간 초과, 저장 실패 | 바로 멈춤 |
     | 서비스 무응답, 흐름 변경, 끝점 변경 | 요청 전에 멈춤 |

     - 실제 Langflow 1.13.3에서 두 경우를 확인했다. Ollama를 끄면 `Error building Component Agent: … AGENT_EXECUTION_FAILED`인 HTTP 500이 나왔다. MCP를 끄면 60초 뒤 `Error building Component MCP Tools: …`인 HTTP 500이 나왔다.
     - 앞의 경우는 Agent 오류로 분류된다. 하지만 다음 구간 전 확인에서 Ollama가 응답하지 않아 멈추고, 멈춤 줄에 직전 문항 결과(`lastCaseOutcome`)를 함께 적는다. 그 문항은 장애 때문에 실패했을 수 있으므로 보고에 적는다.
   - **출력:** ASCII JSON 줄이다.
     - 문항마다 `{"status":"collecting","attempted":…,"outcome":…,"caseSeconds":…}`가 나온다. 첫 문항의 `caseSeconds`에는 자료를 검증하는 몇 초가 포함된다.
     - 끝에는 `chain-finished`(`target_reached`·`complete`·`operator_stop`) 또는 `chain-stopped`(`reason`, `advice`)가 나온다.
   - **멈추고 싶을 때는 Ctrl+C를 한 번** 누른다.
     - 진행 중인 문항을 끝까지 기록한 뒤 멈춘다(최대 16분, `operator_stop`).
     - 두 번 누르면 바로 중단하고, 그 문항은 실패(`aborted`)로 남는다.
     - 창을 닫거나 PC를 끄면 진행 중 문항이 기록되지 않는다. 이어서 실행하면 그 문항을 다시 보낸다(로컬 실행이라 비용은 없다).
   - **동시 실행 금지:** 같은 시리즈를 두 창에서 동시에 돌리지 않는다. 실행 도구가 `.local\ollama.lock`으로 막는다. 이전 실행이 비정상 종료해 잠금이 남았어도, 그 프로세스가 없으면 자동으로 넘겨받는다.
6. **이어서 실행과 96문항:** 같은 명령을 다시 실행하면 가장 최근 쌍에서 이어 간다. 96문항은 `--target 96`으로 실행한다.
   - 멈췄다면 그 이유를 확인해 해결한 뒤 같은 명령을 실행한다.
   - 새 창에서도 API 키나 끝점을 따로 넣을 필요가 없다.
7. **채점.** `chain-finished` 줄에 나온 `latestCapture` 이름을 `$last`에 넣는다. `--report`는 공개할 수 있는 집계이므로 `.local` 밖에 둔다.
   ```
   $last = 'ollama-capture-12'
   $d = Get-Date -Format yyyyMMdd
   node scripts/evaluate-baselines.mjs import --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-template-b0602a1.json --plan .local/baseline-plan-template-b0602a1.json --capture ".local/$last.json" --output ".local/ollama-run-$last.json"
   node scripts/evaluate-baselines.mjs score --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-template-b0602a1.json --plan .local/baseline-plan-template-b0602a1.json --run ".local/ollama-run-$last.json" --details ".local/ollama-scores-$last.json" --report "..\..\reports\baseline-ollama-qwen3-4b-cpu-$d.json"
   ```
   - 예시 번호 `12`는 실제 값으로 바꾼다. 출력 이름이 수집 파일마다 달라서, 다시 채점할 때 `already exists`로 막히지 않는다.
   - 수집기를 직접 부르는 방법(이전 판의 4·5단계)도 그대로 동작한다. 다만 `<flowId>`나 `<날짜>` 같은 자리 표시를 PowerShell에 그대로 붙이면 `<`를 리디렉션으로 해석해 실패하므로, 실행 도구를 쓴다.

**첫 문항이 모델이 아니라 설정 때문에 실패하면 이어서 실행하지 않는다.** 예를 들면 요청 준비의 `REQUEST_PREPARATION_FAILED`, API 키 거부, 꺼진 서버다. 실행 도구는 이런 실패에서 바로 멈춘다.
- 이어서 실행하면 모델과 무관한 그 실패가 96문항 분모에 남는다.
- 설정을 고친 뒤 설정 스크립트를 다시 실행해 `passed`를 확인하고, 새 시리즈(`--series ollama-r2`)로 처음부터 새로 수집한다.
- 중단된 수집·계측 파일은 지우거나 덮어쓰지 않고 `.local`에 둔다. 결과 보고에는 그 파일 이름과 중단 이유를 적는다.

흐름을 Langflow 화면에서 열어 저장하면 저장 내용이 파일과 달라진다. 그러면 실행 도구는 `flow_changed`로, 설정 스크립트는 `EXISTING_FLOW_DIFFERS`로 멈춘다. 모델 선택까지 바뀌면 실행할 때 `OLLAMA_MODEL_SELECTION_INVALID`로 막힌다. 흐름은 API로만 가져와 실행한다.

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
| `npm test` (`services/jeju-mcp`) | 239/239 통과(실행 도구·중단 신호·Windows 재시도 시험 13개 포함) |
| 실제 lfx 1.13.3 생성 확인 | `ChatOllama` 생성 확인(`qwen3:4b`, `127.0.0.1:11434`, `num_ctx` 16384, `num_predict` 2048, `keep_alive` 30m, `reasoning=False`, 요청 제한 600초). 네트워크 연결 0회 |
| **가짜 Ollama 종단 검사** | 실제 Langflow 1.13.3, MCP v2(981곳), 가짜 Ollama로 아래를 모두 실행 |

종단 검사 구성은 다음과 같다. 가짜 Ollama는 같은 HTTP API로 검색 → 조건 확인 → 최종 JSON 순서를 따르는 결정적 서버이고, 저장소에 넣지 않았다.
- 경로: 설정 스크립트 → 수집기(`--local-model-timeout --timeout-ms 960000`) → 요청 준비 → Agent 도구 호출 → 최종 검증 → 가져오기·채점
- 결과: 13문항 모두 정상 응답이었다(점검용 흐름 10문항, 정식 흐름 3문항). 모델 요청에는 `think:false`, `num_ctx` 16384, `num_predict` 2048, `keep_alive` 30m이 실렸다.
- 독립 검토 반영 후 실제 Langflow 1.13.3에서 다시 확인했다.
  - 설정 스크립트의 Langflow 쪽 MCP 확인이 정상 상태에서 `mcpProbe: passed`를 냈다. 저장 토큰이 MCP 토큰과 다른 경우는 `LANGFLOW_MCP_CREDENTIAL_PROBE_FAILED`로 멈췄다(검토자의 별도 인스턴스 실행).
  - 저장 토큰 끝에 공백을 붙이면 설정 스크립트는 `passed`였지만 1문항이 `http_error`로 실패했다. `--replace-mcp-credential` 실행 후(`replaced`) 같은 1문항이 정상 응답했다.
- 이 결과는 연결 확인일 뿐이며 실제 모델의 품질·형식 준수·시간을 뜻하지 않는다.

**Windows 점검과 실행 도구 (2026-11-22).**
- **독립 검토:** 코드 관점과 명령 관점의 검토자 2명이 Windows에서 실패할 수 있는 지점을 각각 11건씩 찾았고, 별도 반박 검증자가 22건을 모두 확인했다. 검증자는 5건을 더 찾았다.
  - 수집기가 실패할 때마다 멈춘다.
  - Langflow를 처음 켠 뒤 비밀번호를 바꾸면 `LOGIN_FAILED`가 된다.
  - `$Matches`에 비밀값이 남는다.
  - 가상환경의 Python 버전을 확인하지 않는다.
  - 로그인이 분당 5회로 제한된다.
  - 확인된 내용은 4·5절과 실행 도구에 반영했다.
- **확인 방법의 한계:** Windows와 PowerShell은 이 작업 환경에서 실제로 실행하지 못했다. 명령은 PowerShell 언어 규칙과 Langflow·Node·pip 소스를 읽어 확인했다.
- **실행 도구 확인:** 실제 Langflow 1.13.3·MCP v2·가짜 Ollama로 확인했다.
  - `--check`: API 키로 흐름을 조회했고, 저장된 흐름이 커밋 파일과 같았다.
  - 3문항을 받은 뒤 다시 실행해 5문항까지 이어 받았다.
  - Ctrl+C(SIGINT)를 한 번 보내면 진행 중 문항을 정상 기록한 뒤 `operator_stop`·종료 코드 130으로 멈췄고, 다시 이어 받았다.
  - 결과 파일의 가져오기와 채점이 통과했다.
  - Ollama를 끄면 Agent의 HTTP 500, MCP를 끄면 MCP Tools의 HTTP 500이 나왔다. 이 실제 응답 본문을 분류 시험에 그대로 넣었다.

남은 것은 다음과 같다.
- 사용자 PC에서의 실제 `qwen3:4b` 10문항 시범과 96문항 실행
- Windows에서의 명령·경로·loopback 전송·Ctrl+C 동작 확인
- Ollama용 런타임 감사(`audit_v2_runtime.py`는 Google 공급자를 가정하므로 별도 변형 필요)
