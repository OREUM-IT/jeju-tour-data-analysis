# 실제 Gemini·Langflow 기준선 실행

기준일: 2026-11-19 (한국 시간). [24 문서](24-agent-baseline-scoring.md)의 오프라인 채점 하네스에 실제 응답 수집 실행기를 연결한다. 사용자 결정에 따라 기존 Gemini 비서와 Gemini를 연결한 Langflow를 비교한다. 이 문서는 실행 구성과 재현 절차를 기록한다. 최종 성공 문항 수·지연·점수는 수집이 끝난 실행의 집계로 확인하며, 구성 확인이나 한 문항 응답만으로 96문항 평가 완료를 표시하지 않는다.

## 1. 비교 대상과 확인된 구성

| 항목 | 기존 Gemini 비서 | Langflow Agent |
|---|---|---|
| 실행 위치 | 기존 Vercel 앱의 `POST /api/assistant` | 로컬 `127.0.0.1:7860`, Langflow 1.13.3 |
| 생성 모델 | 선언한 `gemini-3.8-flash`와 응답의 실제 `model`·`engine`을 대조 | Vertex AI 경로의 `gemini-3.8-flash`, 기존 서비스 계정 사용 |
| 프롬프트 | 기존 앱 프롬프트와 공통 질의·맥락 래퍼 | `jeju-langflow-structured-v1`과 같은 공통 질의·맥락 래퍼 |
| 장소 자료 | 앱 장소 981곳 | 읽기 전용 MCP v1 카탈로그 7,561곳 |
| 조회 도구 | 기존 앱 검색·카탈로그 함수 | MCP의 `places.search`, `places.get`, `constraints.check`; 내장 계산기는 유지 |
| 응답 형식 | `AssistantReply`의 자유 문장·추천 ID·코스 제안 | 지정한 최종 출력 노드의 채점용 JSON을 요청 |
| 현재 날짜 도구 | 서버 시스템 프롬프트가 실제 날짜 사용 | 현재 날짜 도구 비활성화 |

배포 소스 `jeju-tour@e07a93e`에서 평가와 관련된 비서·검색·카탈로그 함수가 고정 참고 커밋 `b0602a1`과 바이트 단위로 일치함을 대조했다. 배포 앱이 반환한 981곳도 평가셋 기반 앱 장소 목록과 일치한다. 이는 해당 코드·자료 대조이며 저장소 전체가 같거나 모든 외부 서비스 응답이 고정됐다는 뜻은 아니다.

**카탈로그 차이는 비교의 중요한 한계다.** 같은 질의·정답 해시를 사용하더라도 Langflow의 MCP v1은 원본 POI 7,561곳을 검색하고, 평가 정답은 앱이 정리·생성한 981곳을 기준으로 계산한다. 앱 생성 장소 일부는 MCP v1에 없고, MCP v1만 가진 ID는 평가에서 후보 밖으로 판정될 수 있다. 따라서 이번 결과는 현재 구성된 두 시스템의 비교이며 모델 자체의 우열이나 같은 검색 자료에서의 Agent 추론 차이를 분리한 실험이 아니다. 카탈로그 v2 도구 연결 후 같은 조건으로 다시 비교해야 한다.

## 2. 키와 서비스 설정

초기에는 사용자가 Vercel의 비밀 환경변수로 저장한 Gemini 키를 비공개 경로로 받아 Langflow 전역 변수에 연결했다. AI Studio 경로에서 `gemini-3.8-flash`와 `gemini-3.5-flash-lite` 호출이 모두 HTTP 503을 반환한 뒤, 사용자 요청에 따라 **기존에 동작하는 서비스 계정으로 Vertex AI 경로를 구성했다**. 새 서비스 계정 키를 만들 필요는 없다. 같은 `gemini-3.8-flash`의 Vertex smoke는 1문항에서 구조화 응답을 반환했고, 수집기 관측 지연은 약 9.9초였다. 이 관측은 전체 평가 완료나 공급자 경로별 일반 성능을 뜻하지 않는다.

현재 사용할 흐름은 [`jeju-tour-agent-vertex.flow.json`](../services/langflow/jeju-tour-agent-vertex.flow.json)이다. **[Vertex 인증·설정 절차](26-langflow-vertex-auth.md)**에 따라 Langflow의 Credential 전역 변수 `JEJU_VERTEX_SERVICE_ACCOUNT_JSON`을 연결한다. 이 값은 기존 서비스 계정 JSON을 `serviceAccount`에, 리전을 `location: "global"`에 두는 비공개 객체다. custom Agent가 `serviceAccount.project_id`의 프로젝트와 서비스 계정 인증을 사용하고, 해당 계정의 `roles/aiplatform.user` 권한을 확인한다. 공개 흐름에는 Credential의 이름만 있고 인증값은 없다.

- 기존 앱의 Gemini 인증과 Langflow Vertex 서비스 계정 인증은 각 대상 서버가 사용한다. 수집 CLI가 `GEMINI_API_KEY`나 서비스 계정 JSON을 앱 요청 본문·헤더로 보내지 않는다.
- Langflow 호출 인증은 별도의 `LANGFLOW_API_KEY`를 `x-api-key` 헤더로 보낸다. 로컬 Gemini 사본에 인증이 필요한 경우에만 `BASELINE_APP_BEARER_TOKEN`을 사용한다.
- CLI 인자·채팅·공개 JSON·PR에 키 값을 넣지 않는다. 비밀 설정은 보호된 환경변수 또는 Git에서 제외된 로컬 설정 파일로 전달한다.
- Langflow는 자동 로그인 없이 loopback에서 실행하고, SSRF 예외는 MCP 연결용 `127.0.0.1`만 허용한다. MCP 연결은 Streamable HTTP를 사용한다. 설치·인증 세부는 [20 문서](20-langflow-agent-builder.md)를 따른다.

키 등록, 모델 목록 조회, `/api/assistant/status`의 설정 응답은 추론 성공을 대신하지 않는다. 실제 호출의 응답·실패·지연을 별도 캡처로 남긴다.

## 3. 요청과 실행 제어

[`baseline-request-adapters.mjs`](../services/jeju-mcp/src/baseline-request-adapters.mjs)는 각 계획의 `request.query`와 `request.context`만 사용한다. 두 시스템 모두 같은 한국어 래퍼에 기준 날짜·날씨·여행 기간과 원문 질의를 넣는다. 정답 후보, 정답 동작, 페르소나 ID, 정답 근거는 보내지 않는다. Gemini의 원본 메시지 600자 제한을 넘으면 질의를 자르지 않고 요청 전에 거부한다.

Gemini 요청은 빈 코스와 새 사용자 메시지로 시작하며, 주어진 여행 기간·비 예보를 원본 API 필드에도 전달한다. Langflow는 매 문항마다 새 무작위 `session_id`를 만들어 이전 문항의 대화 기억이 다음 문항으로 이어지지 않게 한다. 실행 중 모델·프롬프트·도구를 `tweaks`로 바꾸지 않는다.

[`collect-baseline-responses.mjs`](../services/jeju-mcp/scripts/collect-baseline-responses.mjs)의 동작은 다음과 같다.

- `--execute`를 명시해야 실행하며, 기본은 1문항 smoke다. `--max-cases 96`을 명시하면 같은 계획의 96문항을 순서대로 실행한다.
- 동시 호출·자동 재시도·다른 공급자로의 전환은 없다. 첫 실패에서 멈추고 나머지는 `not_executed`로 남긴다. 수집기 자체의 정책이며 원본 앱·모델 SDK 내부 재시도 정책까지 제거하는 것은 아니다.
- Gemini에 한해 `--continue-on-model-mismatch`를 명시하면 정상 응답 형태를 가진 `model_or_engine_mismatch` 실패를 그대로 남기고 다음 미실행 문항으로 진행한다. 다른 모델 응답을 성공으로 승격하거나 실패 문항을 다시 실행하지 않는다. HTTP 오류·타임아웃·잘못된 응답 형태(`invalid_assistant_reply`)·저장 실패에는 이 예외가 적용되지 않는다.
- 기본 타임아웃은 문항당 60초이며 실제 Gemini 앱의 한 대화 제한은 110초다. 그보다 먼저 끊지 않도록 후속 수집에는 `--timeout-ms 120000`을 사용한다. 이전 60초 제한으로 기록된 실패를 지우지 않으며 실행별 제한값은 계측 파일에 남긴다. 응답 크기와 전체 응답 바이트도 제한한다.
- HTTPS를 사용하며 명시적 loopback HTTP만 예외로 둔다. Gemini는 정확한 `/api/assistant`, Langflow는 `/api/v1/run/<FLOW_ID>?stream=false` 경로만 받는다. 리디렉션은 따르지 않는다.
- 모든 입력 해시와 `.local` 실제 경로를 확인하고, 두 출력 파일을 배타적으로 확보한 뒤 요청한다. 기존 파일을 덮어쓰지 않는다.
- 각 시도 완료 후 전체 계획의 캡처·계측을 체크포인트로 저장하고 다음 요청을 보낸다. `.local`의 같은 디렉터리에 권한 `0600` 임시 파일을 쓴 뒤 동기화·이름 교체를 수행한다. 각 파일은 원자적으로 교체되지만 두 파일을 묶은 단일 트랜잭션은 아니다.
- `SIGINT`·`SIGTERM`은 현재 요청을 취소하고 완료·실패·미실행 기록을 보존한다. 실행 실패를 정상 응답으로 바꾸거나 실패 문항을 집계 분모에서 지우지 않는다.

**HTTP 취소가 서버의 모델 실행 취소를 보장하지는 않는다.** 체크포인트는 각 시도가 끝난 뒤 저장하므로, 요청 도중 프로세스가 사라지면 이미 보낸 문항이 마지막 파일에서 `not_executed`로 남을 수 있다. 재개 전에 서버 작업·추적 기록을 확인하고, 실제 요청이 전송됐으나 응답을 잃은 경우에는 근거와 함께 `collector_interrupted` 실패로 기록해 재호출하지 않는다. 이 복구 기록에는 요청·캡처 레코드 해시와 비공개 근거의 `interruptionEvidenceSha256`이 필요하다. 관측하지 못한 `latencyMs`·`httpStatus`는 `null`로 남기며 성공 응답이나 0초 지연을 만들어 넣지 않는다. 체크포인트·재개 기능은 장애 상황에서 정확히 한 번만 과금되는 실행을 보장하지 않는다.

## 4. 한 문항 확인 후 전체 수집

아래 명령은 분석 저장소의 `services/jeju-mcp`에서 실행한다. [19](19-agent-step2-data-foundation.md)·[23](23-agent-step3-evaluation-set.md)·[24](24-agent-baseline-scoring.md) 문서대로 카탈로그·평가셋·계획을 먼저 준비한다. 두 시스템에 같은 `baseline-plan-b0602a1.json`을 사용한다. 꺾쇠 부분은 실제 로컬 흐름·출력 노드·버전으로 바꾸며 인증값은 셸 인자로 넣지 않는다.

```sh
# 기존 Gemini 비서: 실제 응답 1문항 확인
node scripts/collect-baseline-responses.mjs --execute \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan-b0602a1.json \
  --system gemini --model gemini-3.8-flash \
  --prompt-version baseline-context-v1 --tool-version jeju-tour-e07a93e \
  --endpoint https://jeju-tour-six.vercel.app/api/assistant \
  --max-cases 1 --timeout-ms 120000 \
  --output .local/gemini-smoke-capture.json \
  --telemetry .local/gemini-smoke-telemetry.json

# Langflow: LANGFLOW_API_KEY가 프로세스 환경에 설정된 상태에서 실행
node scripts/collect-baseline-responses.mjs --execute \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan-b0602a1.json \
  --system langflow --model gemini-3.8-flash \
  --prompt-version jeju-langflow-structured-v1 \
  --tool-version '<실제 흐름·MCP 버전>' \
  --endpoint 'http://127.0.0.1:7860/api/v1/run/<FLOW_ID>?stream=false' \
  --output-index 0 --component-id '<최종 출력 component_id>' \
  --max-cases 1 --timeout-ms 120000 \
  --output .local/langflow-smoke-capture.json \
  --telemetry .local/langflow-smoke-telemetry.json
```

한 문항에서 실제 모델·출력 노드·도구 연결을 확인한 뒤, 그 성공 캡처와 계측을 `--resume-capture`, `--resume-telemetry`로 함께 넘겨 남은 문항을 수집한다. 이번 Vertex 실행도 성공한 첫 문항을 재사용하고 남은 95문항을 이어가므로 첫 문항 추론을 중복하지 않는다. 처음부터 별도 실행을 만들 때만 `--max-cases 96`을 사용한다. 두 시스템 96문항씩은 수집기 기준 최대 192회 요청이며, 각 Agent가 내부에서 수행하는 모델·도구 호출 수와 같지 않다.

응답의 `engine`이 `keyword`·`none`이거나 실제 Gemini `model`이 선언과 다르면 `invalid_output`으로 기록한다. 수집기가 조용히 대체 모델의 성공으로 인정하지 않는다. Langflow는 선택한 최종 노드에 `Machine` 메시지가 있는지 확인하며, 다른 노드나 입력 echo로 대신하지 않는다. 해당 메시지의 `properties.source.source`가 보고한 모델도 선언과 대조한다. 이번 실제 Vertex 배치의 응답 모델 정보는 흐름 설정과 별도로 확인했다. 이 필드가 없는 이전 캡처를 수용하더라도 실제 모델을 검증한 것으로 취급하지 않는다.

### 4.1 중단된 실행 재개

재개는 기존 성공·실패를 그대로 보존하고 `not_executed`만 채운다. `--max-cases`는 **새 시도 수의 상한**이며 기본값은 여전히 1이다. 이전 계획·시스템·모델·프롬프트·도구 버전·엔드포인트가 일치해야 한다. 원본 두 파일은 변경하지 않고 항상 새 출력 경로를 지정한다. 원본에 실패가 남아 있으면 새 요청이 성공했더라도 CLI 종료 상태는 실패를 포함한 실행을 나타낼 수 있다.

다음은 Gemini의 모델 대체 실패를 유지하면서 남은 문항을 진행하는 명령이다. 인자의 모델·버전은 원본 캡처 값과 정확히 같아야 한다. `--max-cases 96`은 남은 문항을 다시 96개로 늘리는 값이 아니며, 실제 미실행 문항만 처리한다.

```sh
node scripts/collect-baseline-responses.mjs --execute \
  --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan-b0602a1.json \
  --system gemini --model gemini-3.8-flash \
  --prompt-version baseline-context-v1 --tool-version jeju-tour-e07a93e \
  --endpoint https://jeju-tour-six.vercel.app/api/assistant \
  --resume-capture .local/gemini-full-capture.json \
  --resume-telemetry .local/gemini-full-telemetry.json \
  --continue-on-model-mismatch --max-cases 96 --timeout-ms 120000 \
  --output .local/gemini-resumed-capture.json \
  --telemetry .local/gemini-resumed-telemetry.json
```

Langflow 재개는 위 1문항 명령의 모델·흐름·선택 노드를 유지하고 `--resume-capture .local/langflow-smoke-capture.json --resume-telemetry .local/langflow-smoke-telemetry.json --max-cases 95`를 추가한 뒤 새 출력 이름을 지정한다. `--continue-on-model-mismatch`는 Langflow에서는 거부한다.

새 계측은 요청 해시·캡처 레코드 해시와 재개 원본 해시를 기록한다. 해시 필드가 없던 초기 계측은 사건·문항·모델·개수 대조로 연결하고 `legacy_event_binding_checked` 한계를 명시한다. 미실행 표시를 실패나 성공으로 임의 바꾼 파일을 만들어 원본 기록을 숨기지 않는다.

## 5. 응답 판정과 채점

수집 파일에는 원문과 측정값만 있다. 캡처 성공은 응답의 추천·판정이 옳다는 뜻이 아니다. 자유 문장에는 [24 문서](24-agent-baseline-scoring.md)의 사람 판정 또는 명시적으로 표시한 잠정 모델 판정이 필요하다. 순수 구조화 JSON은 원문 그대로 채점하며, 사람이나 모델이 잘못된 ID·조건 단언을 고쳐 넣을 수 없다.

이번 추가 계약의 잠정 판정은 다음 필드를 가진다. 원본 응답 전체의 정규화 JSON 해시와 실제 판정자의 정보를 기록하며, 실제로 사용한 모델을 모르면 확인 가능한 세션 표기를 남기고 정확한 모델 ID를 추정하지 않는다.

```json
{
  "method": "in_session_llm",
  "reviewerId": "<로컬 판정자 별칭>",
  "reviewerProvider": "openai",
  "reviewerModel": "<실제로 확인 가능한 세션 모델 표기>",
  "reviewedAt": "<실제 ISO 시각>",
  "rubricVersion": "llm-review-v1",
  "rawOutputHash": "<원본 응답의 canonicalSha256>",
  "response": {"action": "<실제 답변 동작>", "placeIds": [], "requirementClaims": [], "confirmationNotice": false, "noticeTopics": []}
}
```

`response`에는 실제 응답의 의미와 ID를 옮긴다. 위 빈 배열은 형식 예시이며 실제 응답의 ID나 단언을 없애는 기본값이 아니다. 정답을 보고 응답 의미를 정답에 맞추지 않는다. Gemini 추천 ID 순서는 어댑터가 원문과 대조하고, Langflow 자유 문장 판정에는 같은 `outputSelector`도 넣어야 한다. OpenAI 세션 판정은 Gemini 생성 결과와 구분하며, 사람이 검수한 것처럼 `method: human`으로 표시하지 않는다.

모델 판정이 하나라도 있으면 공개 집계의 `annotationReview.status`는 `provisional`, `humanSpotCheck`는 `pending`이다. **사람의 24문항 표본 검수는 아직 남아 있다.** 표본은 기존 명세의 페르소나별 3문항·슬롯별 2문항 기준을 따른다. 잠정 판정 점수는 사람 검수 완료 점수로 보고하지 않는다.

판정이 포함된 캡처는 기존 명령으로 가져와 채점한다.

```sh
node scripts/evaluate-baselines.mjs import \
  --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan-b0602a1.json \
  --capture .local/gemini-full-reviewed-capture.json --output .local/gemini-full-run.json

node scripts/evaluate-baselines.mjs score \
  --catalog .local/agent-catalog-b0602a1.json --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan-b0602a1.json --run .local/gemini-full-run.json \
  --details .local/gemini-full-scores.json --report ../../reports/baseline-gemini-summary.json
```

Langflow도 같은 계획으로 실행한다. 실패 문항을 포함한 완료율·전체 동작 정확도와, 정상적으로 구조화된 응답만의 지표를 함께 읽는다. 지연은 요청 시작부터 응답 수집까지의 관측 시간이며 앱·도구·네트워크 시간이 포함된다. 실제 청구 금액을 측정하지 못하면 `costUsd`는 `null`로 둔다.

## 6. 자료 보존과 실험 한계

캡처·판정·문항별 점수·거부된 원문이 포함된 상세 계측은 `.local` 전용이다. CLI 로그에는 문항 수·상태·해시만 쓰며 키·장소 이름·ID·질의·응답 원문은 쓰지 않는다. 공개 보고서는 기존 채점기의 집계 출력만 사용한다. 비밀 환경설정과 Langflow DB는 공개 흐름 파일이나 PR에 넣지 않는다.

- **날짜 조건:** 평가 기준일은 `2026-12-07`이다. 기존 Gemini 비서는 서버의 실제 현재 날짜를 시스템 프롬프트에 넣으며 요청 API로 그 시계를 바꿀 수 없다. 공통 래퍼에 평가 날짜를 전달해도 시스템 날짜와 충돌할 수 있다. Langflow의 현재 날짜 도구는 껐지만, 이 변경만으로 두 시스템의 시간 문맥이 완전히 같다고 주장하지 않는다.
- **운영 상태:** 배포 앱의 혼잡 예보, 사용량 한도, 공급자 응답, 내부 fallback은 실행 시점의 상태다. `/api/assistant`는 코스 변경안을 반환할 뿐 그 경로에서 사용자 코스를 저장하지 않지만, 모델 사용량 집계와 혼잡 자료 캐시 갱신 등 통상적인 서비스 부수 효과는 발생할 수 있다.
- **도구·자료:** MCP v1의 7,561곳과 앱의 981곳, 분류·검색 방식·필수 조건 근거가 다르다. 현재 카탈로그의 미확인 조건을 `verified`로 승격하지 않는다. 이 비교로 실제 관광객 만족도나 운영 추천 안전성을 검증한 것은 아니다.
- **판정·보안:** 잠정 모델 판정과 사람이 확인한 판정을 구분한다. 14개 합성 보안 사례의 실행과 부수 효과 계측은 별도 작업이며 정상 요청 수집 성공으로 완료 처리하지 않는다.

실제 수집·채점 결과를 공개할 때는 같은 계획·정답 해시, 실행 시각, 성공·실패·미실행 문항 수, 판정 방법, 위 날짜·카탈로그 차이를 함께 남긴다.

## 7. 실제 실행 결과 (2026-11-19)

### Gemini 고정 모델 기준선

[공개 집계](../reports/baseline-gemini-live-20261119.json)는 같은 계획의 템플릿 96문항을 모두 시도한 결과다. 실행은 한국 시간 08:06:38~08:43:39이며 중간 진단·재개 대기 시간을 포함한다. 정상 응답 92건은 원문을 보존한 세션 내 모델 판정으로 채점했으며 **잠정 결과·사람 검수 대기**다.

| 지표 | 결과 | 해석 |
|---|---:|---|
| 지정 모델 응답 | 92/96 (95.83%) | `gemini-3.8-flash` 단독 응답 |
| 모델 불일치 | 3/96 | 앱이 3.8+3.5-flash-lite로 대체 완료한 응답. 앱 전체의 응답 실패와 달리 고정 모델 비교 계약에서 제외 |
| 수집 시간 초과 | 1/96 | 60초 수집 제한에서 발생. 앱 처리 한도 110초 확인 후 남은 문항은 120초 제한 사용, 실패 문항 재호출 없음 |
| 전체 문항 동작 정확도 | 48/96 (50.00%) | 실패 4건도 분모에 포함 |
| 필수 조건 오판 | 25/30 (83.33%) | 평가 카탈로그가 `unknown`인 조건을 pass 또는 fail로 단언. 실제 세계에서 조건이 거짓임을 입증한 비율은 아님 |
| 허용 후보 밖 ID | 117/333 (35.14%) | 앱 카탈로그에 없는 ID는 0, 해당 요청 후보 밖 ID가 117 |
| 확인 안내 | 4/46 (8.70%) | 안내가 필요한 정상 응답에서 측정 |
| 응답 지연 p50 / p95 | 9.08초 / 31.75초 | 성공 응답 92건만, 수집 대기·네트워크 포함 |
| 비용 | 미측정 (`null`) | 공급자 청구 명세로 확인하지 않음 |

92개 판정의 원문 해시·판정자·시각·모호성 메모는 `.local`에 보존했다. 명시적인 무장애 시설·동선 주장은 접근성 주장으로 옮기되 장소명이나 평탄하다는 표현만으로 추정하지 않는다. 무료 시설이라는 설명만으로 하루 전체 예산 충족을 만들지 않고, 야외 활동 언급만으로 실내 이용 불가를 만들지 않는다. 판정은 응답의 주장 의미를 옮기며 정답을 수정하지 않는다. 사람이 읽을 후보 24개는 성공 응답만으로 페르소나별 3개·슬롯별 2개를 선정했고 실제 검수는 아직 0개다.

Langflow 전체 집계는 별도 실행 완료 후 같은 계획·정답 해시로 추가한다. 한 문항 인증 성공을 전체 평가 완료로 표시하지 않는다.

### 503 진단과 평가 외 호출

Gemini Developer API의 최소 진단 요청 2개에서 3.8-flash·3.5-flash-lite가 각각 HTTP 503 `UNAVAILABLE`와 높은 수요 메시지를 반환했다. 이를 키 오류로 단정하지 않고, 기존 앱에서 사용하는 서비스 계정 OAuth·Vertex AI `global` 경로로 같은 3.8 모델을 연결했다. Vertex 경로의 실제 응답·도구 호출 성공은 확인했지만 공급자 전체의 503이 영구 해소됐다는 뜻은 아니다. 이후 Langflow에서 관측한 504 `DEADLINE_EXCEEDED`는 공급자 요청 60초 제한이며 503과 구분한다.

평가 외 호출도 구분한다. Gemini의 별도 smoke 1건, Developer API 최소 진단 2건, 이전 AI Studio 기반 Langflow smoke 1건이 있었다. Langflow의 `GOOGLE_API_KEY` 저장 시에는 내부 `"test"` 검증과 재시도 경고 5개가 관측됐다. 경고 수는 확정된 청구 요청 수가 아니며 실제 비용은 미측정이다. Vertex smoke는 같은 계획의 첫 문항으로 재개해 중복 합산하지 않았다. HTTP 수집 요청 수는 Agent 내부 모델·도구 호출 수와 다르다.
