# Langflow로 제주 관광 Agent 흐름 만들기

기준일: 2026-11-18 (한국 시간). **사용자 결정:** 시각적 Agent 빌더로 Langflow를 쓴다. 이 문서는 Claude 세션의 실측(관측), 외부 자료의 보고, 제안을 나눠 기록한다. 실측은 분석 작업 공간에서 했고, 사용자 PC·운영 배포·실제 모델 추론은 검증하지 않았다.

## 1. 선택 근거와 버전

| 구분 | 내용 |
|---|---|
| 보고 | Flowise는 2026-07-29 개발이 멈췄고 2026-08-31 지원이 끝났다([공식 안내](https://flowiseai.com/sunset)). 이후 3.1.4 이하의 고위험 보안 권고가 수정 버전 없이 공개됐다는 보도가 있어 후보에서 뺐다. |
| 관측 | PyPI의 Langflow 최신 버전은 1.13.3이다(`pip index versions langflow`). 이 문서의 검증은 1.13.3으로 했다. |
| 보고 | 1.0.0~1.12.8에는 인증 우회와 비인증 코드 실행 취약점이 있고, 1.13.0에서 고쳐졌다([CSA 연구 노트](https://labs.cloudsecurityalliance.org/research/csa-research-note-langflow-1-13-unauth-rce-20261103-csa-styl/), [CSO 보도](https://csoonline.com/article/4244226/critical-langflow-flaws-enable-authentication-bypass-and-rce.html)). **1.13.0 미만은 쓰지 않는다.** |
| 보고 | MCP stdio 명령 주입 취약점 이력이 있다([보도](https://www.esecurityplanet.com/news/news-langflow-critical-rce-vulnerability-patch/)). 이 프로젝트는 Streamable HTTP만 쓰고 stdio MCP 서버는 추가하지 않는다. |
| 보고 | 호스팅판은 2026년 4월에 종료됐다는 보도가 있어 로컬 설치를 기준으로 한다. |

## 2. 구성

```
[Langflow 1.13.x, 127.0.0.1:7860]
  Chat Input → Agent(모델·키는 사용자 설정) → Chat Output
                 ↑ tools
             MCP Tools(Tool Mode, 서버 이름 jeju-mcp)
                 │ Streamable HTTP + Authorization: Bearer {{JEJU_MCP_TOKEN}}
                 ▼
[jeju-mcp 읽기 전용 서버, 127.0.0.1:8788/mcp] → places.search · places.get · constraints.check
```

MCP 도구는 실제 카탈로그의 ID를 반환하고 근거가 없으면 `unknown`을 반환한다. 이 보장은 도구의 입력·출력 범위이며 Agent가 최종 설명에서 다른 장소나 조건 판정을 만들어내는 것을 막지는 않는다. 원래 사용자 필수 조건, 최종 장소 ID·근거·판정은 별도 서버 검증이 필요하다. 현재 시작 흐름에는 계산기·현재 날짜 도구도 있으므로 운영 허용 목록은 MCP 세 도구뿐 아니라 Agent 전체 도구를 대상으로 검토한다. [22 탈옥·프롬프트 주입 방어](22-jailbreak-and-prompt-injection-defense.md)를 적용할 계획이며 해당 운영 검증기는 아직 미구현이다.

## 3. 설치와 실행 (Windows·macOS 공통)

이번 검증은 Python 3.13과 uv로 했다. 아래 명령은 분석 저장소 루트를 기준으로 한다.

```sh
# 1) Langflow 1.13.3 설치 (관측: 약 1분 40초). .langflow-venv는 .gitignore 대상이다.
uv venv --python 3.13 .langflow-venv
source .langflow-venv/bin/activate        # Windows: .langflow-venv\Scripts\activate
uv pip install "langflow==1.13.3"

# 2) 설정 파일: 예시를 복사해 비밀번호를 바꾼다. langflow.env는 Git에 올리지 않는다.
cp services/langflow/langflow.env.example services/langflow/langflow.env

# 3) jeju-mcp 실행 (자세한 절차는 services/jeju-mcp/README.md)
cd services/jeju-mcp
node scripts/configure-local.mjs --catalog .local/catalog.json   # .env.local이 없을 때만
node --env-file=.env.local src/server.mjs

# 4) 다른 터미널의 저장소 루트에서 같은 가상환경을 활성화하고 Langflow 실행 후 http://127.0.0.1:7860 접속
langflow run --env-file services/langflow/langflow.env
```

`LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`이 없으면 Langflow가 로컬 MCP 연결을 막는다(5절). Langflow Desktop의 환경변수 설정 위치는 이번에 확인하지 못했다. 그래서 이 설정이 확실히 들어가는 uv 설치를 기준으로 한다.

## 4. Langflow 화면에서 연결

1. **전역 변수:** `JEJU_MCP_TOKEN`을 Credential 유형으로 만들고, 값에 jeju-mcp `.env.local`의 `MCP_SERVICE_TOKEN`을 넣는다. 토큰을 흐름·채팅·Git에 붙이지 않는다.
2. **MCP 서버 등록:** 설정의 MCP 서버 화면이나 MCP Tools 노드의 Add MCP Server에서 HTTP/SSE 방식을 고른다. 이름은 `jeju-mcp`, URL은 `http://127.0.0.1:8788/mcp`, 헤더는 `Authorization` = `Bearer {{JEJU_MCP_TOKEN}}`로 넣는다. 흐름 파일이 이 이름으로 서버를 찾으므로 이름이 같아야 한다.
3. **흐름 가져오기:** [`services/langflow/jeju-tour-agent.flow.json`](../services/langflow/jeju-tour-agent.flow.json)을 가져온다(Import).
4. **모델 선택:** Agent 노드에서 공급자와 모델을 고르고 키를 전역 변수로 넣는다. 흐름 파일에는 모델 키가 없다. 공급자를 고를 때는 [모델 레지스트리](17-ai-agent-model-registry.md)의 비용·전송 경계를 따른다.
5. **시험:** Playground에서 "서귀포 박물관 찾아 줘", "성산일출봉이 실내야?"처럼 물어본다. 실내 여부는 `unknown`이라 "방문 전 확인 필요"로 답해야 정상이다.

메뉴 이름은 Langflow 버전에 따라 다를 수 있다. 같은 설정은 API(`POST /api/v2/mcp/servers/jeju-mcp`, `POST /api/v1/variables/`)로도 할 수 있으며, 이번 실측은 API로 했다.

## 5. 실측 기록 (2026-11-18, 분석 작업 공간)

| 확인 | 결과 |
|---|---|
| 설치·실행 | Langflow 1.13.3을 uv로 설치하고 `langflow run --backend-only`로 실행. `/api/v1/version` = 1.13.3 |
| SSRF 차단 | 허용 목록 없이 `127.0.0.1` MCP에 연결하면 `SSRFProtectionError`. `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`로 해결 |
| 비밀 키 형식 | `LANGFLOW_SECRET_KEY`가 Fernet 형식이 아니면 전역 변수 저장이 HTTP 500으로 실패 |
| 토큰 없는 연결 | jeju-mcp가 거부 |
| Langflow MCP 클라이언트(`lfx`) | 도구 3개와 입력 스키마 발견. `places.search` 호출 성공, `constraints.check`는 `unknown`, 잘못된 입력은 `-32602` 오류로 표시 |
| 서버 등록 API | `jeju-mcp` 등록 후 목록에 `streamable_http`, `toolsCount: 3`. 헤더 값은 `********`로 가려져 반환 |
| 시작 흐름 | Langflow 자체 컴포넌트 갱신 API로 생성. 가져오기 성공, MCP Tools 노드 단독 빌드 `valid: true`, 시험 사본 삭제 |
| 비밀값 | 흐름 파일에 MCP 토큰·Langflow 비밀번호·모델 키 없음. 생성 스크립트와 `check_repository.py`가 함께 검사 |
| 미검증 | 실제 모델로 Agent 실행(모델 키 없음), Langflow Desktop, Docker 실행, 사용자 Windows·macOS 실기 |

## 6. 보안·데이터 경계

- `127.0.0.1`에서만 실행하고 인터넷·사설망에 열지 않는다. `LANGFLOW_AUTO_LOGIN=false`를 유지한다. 1.13.0 이상을 쓰고 새 보안 공지를 확인한다.
- SSRF 허용 목록에는 `127.0.0.1`만 넣는다. 사설망 전체나 CIDR 범위를 열지 않는다.
- MCP는 Streamable HTTP만 쓴다. stdio 서버(명령 실행)를 추가하지 않는다.
- 토큰·모델 키는 전역 변수(Credential)로만 넣는다. 흐름을 내보내기 전에 키 필드가 비었는지 확인한다.
- `.local`의 카탈로그·앱 장소 덤프를 Langflow 지식베이스에 넣으면 외부 임베딩·모델 API로 전송될 수 있다. 라이선스 검토 전에는 넣지 않는다.
- 외부 모델을 쓰면 사용자 질문과 도구 결과(장소 이름·좌표·출처)가 공급자에게 전송된다. 전송 범위와 비용은 [모델 레지스트리](17-ai-agent-model-registry.md) 7절을 따른다.

## 7. 다음 단계와의 연결 (제안)

- **3단계 평가셋:** 같은 질의 묶음으로 Langflow 흐름과 기존 Gemini 비서를 함께 채점한다. Langflow 흐름은 API로 실행할 수 있다.
- **4단계 도구:** 카탈로그 v2 기반 도구(테마 랭킹, 근거 등급 조건 검사)를 jeju-mcp에 더하면 흐름 구조는 그대로 둔다. MCP Tools 노드에서 도구 목록만 새로 읽으면 된다.
- **5단계 LAG 비교:** 하위 질문 분해 → 순차 도구 호출 → 답할 수 없으면 중단하는 구성을 Langflow 흐름으로 만들어 일반 도구 호출 Agent와 비교한다.

## 8. 시작 흐름 재생성

Langflow 버전을 올린 뒤에는 실행 중인 로컬 Langflow로 흐름을 다시 만든다. 이 스크립트는 MCP 노드 생성, 가져오기·빌드 검사, 시험 사본 삭제, 비밀값 검사를 한 번에 한다.

```sh
python services/langflow/build_starter_flow.py \
  --env-file services/langflow/langflow.env \
  --starter "<langflow site-packages>/langflow/initial_setup/starter_projects/Simple Agent.json" \
  --output services/langflow/jeju-tour-agent.flow.json \
  --mcp-env-file services/jeju-mcp/.env.local
```
