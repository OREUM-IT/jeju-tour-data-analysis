# Langflow에서 Vertex AI 인증 연결하기

기준일: 2026-11-19 (한국 시간). 기존 제주 앱과 같은 **Gemini `gemini-3.8-flash`를 Vertex AI 경로로 연결**하는 안내다. [재사용 흐름 JSON](../services/langflow/jeju-tour-agent-vertex.flow.json)은 서비스 계정 자격증명을 Langflow 전역 변수에서 읽으며, 실제 키나 계정 식별자를 포함하지 않는다.

이번 작업에서 Gemini Developer API는 같은 모델에 HTTP 503·높은 수요 오류를 반환했다. 기존 앱이 사용하는 Vertex AI 경로에 맞춘 뒤 Langflow 시험 요청 1건이 약 9.9초에 완료됐고, MCP 도구 호출과 구조화 응답 가져오기를 확인했다. 이는 한 요청의 연결 성공이며 전체 96문항 완료나 추천 품질 검증을 뜻하지 않는다. 실행·채점 절차는 [25 문서](25-live-baseline-execution.md)를 따른다.

## 1. 두 인증 방식의 차이

| 방식 | 준비할 값 | 이 흐름에서 사용 |
|---|---|---|
| Google AI Studio / Gemini Developer API | API 키 | 사용하지 않음 |
| Google Cloud Vertex AI | 서비스 계정 JSON, 해당 프로젝트의 Vertex AI 사용 권한 | 사용 |

AI Studio에서 만든 키를 서비스 계정 JSON 칸에 넣을 수는 없다. 같은 Gemini 모델 이름이어도 호출 경로·인증·가용성이 다르다. 이번 변경은 모델을 바꾸지 않고 호출 경로를 기존 앱과 맞춘 것이다.

## 2. Google Cloud 준비

1. 연결할 Google Cloud 프로젝트에서 **Vertex AI API**(`aiplatform.googleapis.com`)가 활성화되어 있는지 확인한다. 해당 프로젝트에서 Vertex AI 사용이 가능해야 한다.
2. **IAM 및 관리자 → 서비스 계정**에서 해당 앱에 사용할 서비스 계정을 선택한다. 프로젝트 IAM에서 이 계정에 **Vertex AI 사용자**(`roles/aiplatform.user`) 역할이 있어야 한다.
3. 이미 앱에서 사용하는 유효한 서비스 계정 JSON이 있으면 그 보안 설정을 재사용한다. 새로 발급해야 한다면 서비스 계정의 **키 → 키 추가 → 새 키 만들기 → JSON**에서 내려받는다. 키 생성이 조직 정책으로 제한된 환경에서는 관리자에게 허용된 인증 방식을 확인한다.
4. JSON의 `type`은 `service_account`이고 `project_id`, `private_key`, `client_email`, `token_uri` 등이 있어야 한다. 내려받은 JSON을 임의로 자르거나 재작성하지 않는다.

실제 JSON은 Langflow의 Credential 전역 변수에 저장한다. 채팅·흐름 JSON·Git·보고서에는 붙이지 않는다. 이 작업에서는 서비스 계정 RSA 키 파싱과 OAuth 토큰 갱신 성공을 먼저 확인한 뒤 모델 요청을 실행했다.

## 3. Langflow 전역 변수 설정

[20 문서](20-langflow-agent-builder.md)의 로컬 Langflow 1.13.3과 jeju-mcp를 실행한다. Langflow는 `127.0.0.1:7860`, MCP는 `127.0.0.1:8788/mcp`를 사용하며 로그인과 MCP 인증을 유지한다.

Langflow **Settings → Global Variables**에서 아래 변수를 만든다. 유형은 둘 다 **Credential**이다.

| 이름 | 값 |
|---|---|
| `JEJU_MCP_TOKEN` | 로컬 jeju-mcp의 `MCP_SERVICE_TOKEN` |
| `JEJU_VERTEX_SERVICE_ACCOUNT_JSON` | 아래 구조로 감싼 서비스 계정 JSON |

`JEJU_VERTEX_SERVICE_ACCOUNT_JSON`의 정확한 구조는 다음과 같다. 예시의 `serviceAccount` 객체를 Google Cloud에서 받은 **전체 JSON 객체**로 바꾼다. 문자열로 한 번 더 감싸지 않는다.

```json
{
  "serviceAccount": {
    "type": "service_account",
    "project_id": "YOUR_PROJECT_ID",
    "private_key_id": "YOUR_PRIVATE_KEY_ID",
    "private_key": "YOUR_COMPLETE_PEM_PRIVATE_KEY",
    "client_email": "YOUR_SERVICE_ACCOUNT_EMAIL",
    "client_id": "YOUR_CLIENT_ID",
    "token_uri": "https://oauth2.googleapis.com/token"
  },
  "location": "global"
}
```

이 예시는 형식 설명용이며 인증할 수 있는 값이 아니다. 실제 내려받은 JSON의 추가 필드는 그대로 보존해도 된다. 서비스 계정 JSON만 통째로 넣거나 `GEMINI_API_KEY`만 넣으면 이 흐름의 입력 계약에 맞지 않는다. **프로젝트는 `serviceAccount.project_id`에서 읽으므로 바깥쪽 `projectId` 필드는 필요 없다.** `location`은 현재 검증한 `global`을 사용한다.

커스텀 변수 이름을 쓰는 이유도 있다. 이 Langflow 버전은 `GOOGLE_API_KEY`를 저장할 때 기본 카탈로그 모델에 `"test"`를 보내는 내부 검증을 실행한다. 이번 작업에서도 이 검증의 429 재시도 경고 5개를 관측했다. 위 `JEJU_VERTEX_SERVICE_ACCOUNT_JSON`은 해당 공급자 키 자동 검증 경로에 해당하지 않으며, 흐름 가져오기와 실제 모델 실행을 구분할 수 있다.

## 4. MCP 연결과 흐름 가져오기

1. Langflow의 MCP 서버 설정에 이름 **`jeju-mcp`**, 주소 **`http://127.0.0.1:8788/mcp`**를 등록한다. 전송 방식은 **Streamable HTTP**이며 API 설정의 모드 값은 정확히 `Streamable_HTTP`다.
2. 헤더는 `Authorization: Bearer {{JEJU_MCP_TOKEN}}`로 지정한다. `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1` 설정은 20 문서를 따른다.
3. [jeju-tour-agent-vertex.flow.json](../services/langflow/jeju-tour-agent-vertex.flow.json)을 Langflow에서 **Import**한다. 이 파일의 Agent는 Vertex AI 인증과 요청 제한을 추가한 커스텀 Python 컴포넌트다. 일반 Agent 노드로 교체하면 이 설정이 사라진다.
4. Agent의 **Vertex service-account credential** 필드가 `JEJU_VERTEX_SERVICE_ACCOUNT_JSON` 전역 변수를 참조하는지 확인한다. 실제 키를 노드 값으로 입력하지 않는다. 모델은 **Google Generative AI / `gemini-3.8-flash`**다.
5. MCP Tools 노드에서 `places.search`, `places.get`, `constraints.check` 세 도구를 확인한 뒤 시험 요청을 실행한다. 최종 출력은 `action`, `placeIds`, `requirementClaims`, `confirmationNotice`, `noticeTopics`를 가진 JSON이다.

흐름 내부는 서비스 계정으로 OAuth를 사용하고 `vertexai=True`, `google_api_key=None`으로 모델을 만든다. Google SDK의 `max_retries=1`은 **초기 요청 1회·재시도 0회**를 뜻한다. `0`은 기본 재시도를 사용하므로 같은 뜻이 아니다. 요청 제한은 60초이며 여러 도구·모델 호출을 포함한 전체 Agent 실행 시간 제한과는 다르다.

## 5. 연결이 안 될 때

| 관측 | 확인할 것 |
|---|---|
| 서비스 계정 JSON·PEM 파싱 오류 | 전체 JSON이 들어 있는지, `serviceAccount`가 객체인지, PEM의 시작·끝과 줄바꿈이 보존됐는지 |
| OAuth 갱신 실패 | 키가 폐기됐는지, 서비스 계정이 활성 상태인지, OAuth 토큰 엔드포인트에 연결되는지 |
| Vertex AI 403 | 프로젝트의 API 활성화, 서비스 계정의 `roles/aiplatform.user` 권한, 모델 접근 조건 |
| 모델 404 | 모델 이름과 프로젝트·위치 조합. 승인 없이 다른 모델로 바꾸지 않고 실행 기록에 원인을 남김 |
| 503·높은 수요 | 해당 경로의 일시적 가용성 문제. 자동 무한 재시도 대신 실패 기록과 제한된 재검사를 사용 |
| MCP 도구가 안 보임 | 서버 이름, `Streamable_HTTP` 모드, 토큰 전역 변수, 루프백 SSRF 허용 설정 |

이 흐름은 현재 v1 MCP 카탈로그 7,561곳을 사용한다. 평가셋의 앱 장소 981곳과 후보 범위가 다르며, 근거 없는 조건은 `unknown`으로 남는다. 연결 성공이 이 차이나 최종 답변의 오류 가능성을 없애지는 않는다.
