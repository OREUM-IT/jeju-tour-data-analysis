# AI Agent 기준선 채점: 로컬 하네스와 응답 가져오기

기준일: 2026-11-19 (한국 시간). [3단계 평가셋](23-agent-step3-evaluation-set.md)의 동일한 질의와 정답 규칙으로 기존 Gemini 비서와 Langflow 응답을 비교할 **오프라인 하네스**다. 요청 준비, 응답 가져오기, 채점, 정답 기반 자체 검사를 구현했다. 모델이나 Langflow 서버를 직접 호출하는 실행기는 아직 없으며, 실제 두 시스템의 비교 점수와 운영 Agent의 성능을 측정한 상태가 아니다.

## 1. 이번 환경에서 복원한 데이터

- 참고 앱은 `OREUM-IT/jeju-tour@b0602a175c1d08959b7187da2b29ac450b52e0ba`로 고정했다. 참고 체크아웃은 그대로 두고 별도 사본만 빌드했다.
- 앱 장소 981곳(원시 POI 863곳·앱 생성 118곳)의 근거 등급 카탈로그를 복원했다. 커버리지 집계는 기존 보고서와 일치하며 `verified` 속성은 여전히 0건이다.
- 평가셋은 96문항이고 정답 해시는 `b982194547edc386b1bfb5d9588cbe607dc99fbf960072b9b20221904f21217d`로 기존과 일치한다. 생성기의 `--check`로 평가셋·집계 파일 재현성을 확인했다.
- 이전 세션에서 작성한 질의 파일은 Git에 없으므로 이번에는 **템플릿 질의만 96건**이다. `contentSha256.queries`는 `null`이다. 같은 템플릿을 작성 질의라고 바꾸어 두 문체를 비교하지 않는다.
- [자체 검사 집계](../reports/baseline-harness-self-check.json): 96문항 채점 완료, 허용 ID 위반·unknown 오판 0, 동작 정확도·재현율·정밀도 1. 정답에서 만든 응답이므로 모델 성능이 아니다. 지연·비용은 미측정 `null`이다.
- Node 24.22.1에서 서비스·하네스 95개와 Gateway 10개 테스트, 문서·메타데이터 검사 10개 항목을 확인했다. 원본 자료 없이 합성 데이터로 검사하며 실제 모델·보안 공격 실행은 포함하지 않는다.

전체 데이터 복원은 [19 문서 5절](19-agent-step2-data-foundation.md#5-재현-절차), 평가셋 생성은 [23 문서 8절](23-agent-step3-evaluation-set.md#8-재현-절차)을 따른다. 이 하네스를 실행하려면 `services/jeju-mcp/.local/agent-catalog-b0602a1.json`과 `evaluation-set-b0602a1.json`이 있어야 한다.

## 2. 파일과 데이터 경계

| 파일 | 역할 |
|---|---|
| [`baseline-evaluation.mjs`](../services/jeju-mcp/src/baseline-evaluation.mjs) | 검증된 평가셋으로 요청 계획 생성, 해시 결합, 전체 문항 검사, 시스템·문체별 채점 |
| [`baseline-adapters.mjs`](../services/jeju-mcp/src/baseline-adapters.mjs) | Gemini 응답과 명시적으로 선택한 Langflow 메시지를 채점 계약으로 변환 |
| [`evaluate-baselines.mjs`](../services/jeju-mcp/scripts/evaluate-baselines.mjs) | `prepare`, `import`, `score`, `self-check` 명령 |
| `.local/baseline-plan.json` | 실제 질의·맥락, 요청별 해시와 문항 연결 |
| `.local/*-capture.json` | 원본 응답, 사람의 판정, 측정값, 실행 메타데이터 |
| `.local/*-run.json`, `.local/*-scores.json` | 구조화된 응답·판정 기록과 문항별 점수 |
| `--report`로 지정한 JSON | 공개 가능한 집계·해시·시각. 장소 이름·장소 ID·질의·응답 원문 없음 |

카탈로그, 평가셋, 계획, 응답, 상세 점수는 `.local` 안의 JSON만 받는다. 심볼릭 링크의 실제 경로도 검사한다. 공개 집계 경로는 `.local` 밖이어야 하며 입력·출력이 같은 경로이면 거부한다. 새 산출물은 기존 파일을 덮어쓰지 않는다.

채점 대상에 보낼 것은 계획의 각 `cases[].request`에 있는 **`query`와 `context`뿐**이다. `caseId`, `itemId`, 문체·해시는 하네스 내부 연결용이다. 정답 후보, 정답 동작, 페르소나 ID, 조건 판정, 정답 생성에 사용한 사실 요약은 모델 입력에 추가하지 않는다. 대상 시스템은 자기 도구와 데이터로 답해야 한다. `context`에 있는 기준일·비 예보·여행 기간은 같은 의미로 전달하고, 시스템별 변환 방식은 로컬 `promptVersion`에 기록한다.

## 3. 요청 준비와 자체 검사

아래 명령은 모두 분석 저장소의 `services/jeju-mcp`에서 실행한다. 네트워크나 모델 호출은 없다.

```sh
# 1) 템플릿 96문항 요청 계획
node scripts/evaluate-baselines.mjs prepare \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --variants template --output .local/baseline-plan.json

# 2) 정답 기반 채점기 자체 검사
node scripts/evaluate-baselines.mjs self-check \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan.json \
  --output .local/oracle-run.json --details .local/oracle-scores.json \
  --report ../../reports/baseline-oracle-summary.json

# 3) 저장된 실행을 다시 채점해 파일과 바이트 비교
node scripts/evaluate-baselines.mjs score \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan.json --run .local/oracle-run.json \
  --details .local/oracle-scores.json \
  --report ../../reports/baseline-oracle-summary.json --check
```

`self-check`는 정답에서 응답을 만든다. 결과의 `mode`는 `oracle_self_check`, `resultType`은 `scorer_self_check_not_model_quality`다. 높은 점수는 정답과 채점기의 일관성을 뜻하며 **Gemini·Langflow·추천 Agent의 성능이 아니다**. 모델은 없으므로 `modelId`는 `none`, 미측정 지연·비용은 `null`이다.

작성 질의 96건을 다시 확보하고 23 문서의 규칙으로 평가셋에 붙인 경우에만 `--variants template,written`을 쓴다. 하나라도 작성 질의가 없으면 계획 생성을 거부한다. 작성 질의와 템플릿이 같은 문항 수도 `wording.identicalWrittenItems`로 기록해 문체 차이가 실제로 있는지 구분한다.

## 4. 실제 응답 캡처 계약

모델 공급자·키 제공 경로·전송 범위는 아직 정해지지 않았다. 실제 호출은 이 조건이 정해진 뒤 별도 실행기로 진행해야 한다. 키를 캡처 JSON, Git, PR, 채팅에 넣지 않는다. 이 하네스의 `import`는 로컬 파일을 읽을 뿐 과금 호출을 만들지 않는다.

캡처 파일의 전체 구조는 다음과 같다. 꺾쇠 안 값은 실제 계획·모델·버전으로 채운다. `records`에는 계획의 모든 요청이 정확히 한 번씩 있어야 한다. 아래 빈 배열만으로는 가져오기가 되지 않는다.

```json
{
  "schemaVersion": "1.0",
  "kind": "jeju-baseline-capture",
  "planSha256": "<계획의 planSha256>",
  "system": {
    "id": "gemini",
    "modelId": "<실제 호출한 모델 ID>",
    "promptVersion": "<질의·맥락 전달 방식과 프롬프트 버전>",
    "toolVersion": "<원본 앱 커밋 또는 흐름·MCP 계약 버전>",
    "adapterVersion": "1.0.0"
  },
  "startedAt": "2026-11-19T00:00:00.000Z",
  "completedAt": "2026-11-19T00:01:00.000Z",
  "records": []
}
```

시각은 실제 실행의 UTC ISO 시각이어야 한다. Langflow는 `system.id`를 `langflow`로 지정하고 실제 연결 모델·흐름·프롬프트·도구 버전을 기록한다. 메타데이터는 실행자의 기록이며, 파일을 가져왔다는 사실만으로 해당 서버에서 추론했다는 독립 증거가 생기지는 않는다.

각 성공 레코드는 `caseId`, 그 계획의 `requestSha256`, 전체 `raw` 응답을 가진다. `measurements`는 호출자가 측정한 `latencyMs`, `costUsd`만 받을 수 있다. 모르면 생략하거나 `null`로 둔다. 모델이 본문에 쓴 지연·비용을 측정값으로 채택하지 않는다.

### 4.1 기존 Gemini 비서

고정 원본 `prototype/src/assistant.ts`의 `runAssistant`가 반환하는 `AssistantReply`를 가져온다. 주요 필드는 `reply`, `places`, `action: {type, placeIds}`, `engine`, `model`이다. 실제 캡처에는 원본 응답 전체를 보존한다.

- `places[].id`를 표시 순서대로 유지하고, `action.type`이 `add`·`replace`이면 아직 없는 `action.placeIds`를 뒤에 붙인다. 카탈로그나 정답 후보 밖 ID도 지우지 않고 채점한다.
- 화면 표시와 코스 제안에 같은 ID가 있으면 하나로 합치고 중복 참조 수를 로컬 판정 기록에 남긴다. `remove`의 ID는 삭제 대상이라 추천 목록에 추가하지 않는다.
- `action.type`은 코스 조작 종류이므로 채점의 `action`으로 해석하지 않는다. 문장 의미는 아래 사람 판정으로 따로 기록한다.
- CLI는 `raw.engine === "gemini"`이고 `raw.model === system.modelId`인 응답만 받는다. 기존 앱의 `keyword`·`none` fallback을 Gemini 모델 결과로 집계하지 않는다. fallback 발생은 `invalid_output` 등 해당 오류 레코드로 보존하거나, 별도의 기준선 설계가 먼저 필요하다.

### 4.2 사람 판정: `manual-v1`

Gemini 자유 문장과 일반 Langflow 채팅 문장은 다음 `review`를 요구한다. 아래 장소 ID는 형식을 설명하는 합성 예시이며 실제 평가 결과가 아니다.

```json
{
  "caseId": "CASE-001-template",
  "requestSha256": "<계획의 해당 요청 해시>",
  "raw": {"reply": "<원본 응답>", "places": [{"id": "PLACE_FIXTURE_A"}], "action": {"type": "none", "placeIds": []}, "engine": "gemini", "model": "<실제 모델 ID>"},
  "review": {
    "method": "human",
    "reviewerId": "reviewer-01",
    "reviewedAt": "2026-11-19T00:02:00.000Z",
    "rubricVersion": "manual-v1",
    "rawOutputHash": "<전체 raw의 canonicalSha256>",
    "response": {
      "action": "cannot_confirm",
      "placeIds": ["PLACE_FIXTURE_A"],
      "requirementClaims": [{"requirement": "wheelchair", "placeId": null, "verdict": "unknown"}],
      "confirmationNotice": true,
      "noticeTopics": ["wheelchair_unverified"]
    }
  },
  "measurements": {"latencyMs": null, "costUsd": null}
}
```

판정자는 정답에 맞추어 응답을 수정하지 않고 **실제로 답한 내용**을 옮긴다.

| 필드 | 옮기는 기준 |
|---|---|
| `action` | 추천이면 `recommend`, 필수 조건을 확인할 수 없어 확인 필요 후보를 제시하면 `cannot_confirm`, 답변 전에 정보를 되물으면 `ask_clarification`, 가능한 후보가 없다고 안내하면 `impossible`. 원본의 코스 조작 이름이나 단일 낱말로 결정하지 않는다 |
| `placeIds` | Gemini는 위 ID 순서 규칙을 정확히 보존한다. Langflow 자유 문장은 실제 추천 ID를 옮긴다. 잘못된 ID를 정답으로 교정하거나 이름으로 ID를 새로 만들어 채우지 않는다 |
| `requirementClaims` | 실제 단언한 휠체어(`wheelchair`)·실내(`indoor_required`)·영업시간(`open_at`)·비용(`budget_cap`) 조건을 `pass`, `fail`, `unknown`으로 기록한다. 장소를 특정하지 않은 주장은 `placeId: null`로 응답의 모든 장소에 적용한다. 주장이 없으면 `[]` |
| `confirmationNotice`, `noticeTopics` | 실제 확인 필요 안내가 있는지와 다룬 주제를 기록한다. 안내가 없으면 `false`, `[]`. 허용 주제는 23 문서 및 `evaluation-set.mjs`의 `NOTICE_TOPICS`를 따른다 |

`requirementClaims`, `confirmationNotice`, `noticeTopics`는 판정을 생략한 것과 관측된 부재를 구분하기 위해 명시해야 한다. 판정자 별칭·판정 시각·규칙 버전은 `.local`에 보관한다. `rawOutputHash`는 원본 JSON 바이트 해시가 아니라 [`evaluation-rng.mjs`](../services/jeju-mcp/src/evaluation-rng.mjs)의 `canonicalSha256(raw)`다. 원본이 바뀌면 기존 판정은 재사용할 수 없다.

사람 판정의 정확성·판정자 일치도는 자동 검증하지 않는다. 명확히 옮길 수 없는 응답을 정답에 맞추어 보완하지 말고 재검토하거나 `invalid_output`으로 남긴다. 자동 LLM 판정기는 구현하지 않았다.

### 4.3 Langflow 출력 선택

Langflow 캡처 레코드에는 다음 선택자를 명시한다.

```json
"outputSelector": {"outputIndex": 0, "componentId": "<실제 최종 출력 component_id>"}
```

어댑터는 `raw.outputs[outputIndex].outputs`에서 `component_id`가 정확히 한 번 일치하는 노드의 `results.message`만 읽는다. `sender`가 `Machine`이고 `text`가 문자열이어야 한다. 입력 echo, 로그, artifacts 또는 다른 출력 노드로 대체하지 않는다.

선택한 `text`가 채점 응답 구조의 순수 JSON 객체면 구조화 모드로 가져올 수 있다. 코드 펜스로 감싼 JSON과 일반 문장은 자동으로 해석하지 않고 사람 판정을 요구한다. 사람 판정이면 `review.outputSelector`에도 동일한 선택자를 넣어 판정 대상을 고정한다. 구조화 응답에도 `requirementClaims`, `confirmationNotice`, `noticeTopics`를 명시해야 하며 응답의 `itemId`가 있으면 해당 문항과 같아야 한다.

순수 구조화 JSON의 장소 ID·조건 판정은 사람 판정으로 덮어쓸 수 없다. 계약을 어긴 구조화 출력은 오류로 남겨 잘못된 모델 응답을 사후 교정하지 않는다.

### 4.4 실패 기록

호출 실패나 미실행 문항을 삭제하면 분모가 달라지므로 다음처럼 남긴다.

```json
{"caseId": "CASE-002-template", "requestSha256": "<해당 요청 해시>", "error": {"code": "timeout"}}
```

허용 코드는 `timeout`, `transport_error`, `invalid_output`, `not_executed`다. 오류 레코드에는 응답·사람 판정·측정값을 함께 넣지 않는다. 오류 내용에 원본 공급자 메시지나 비밀값을 넣지 않는다. 모든 실패를 포함해 계획과 같은 문항 수를 유지하며 중복·누락·알 수 없는 문항·요청 해시 불일치는 거부한다.

## 5. 응답 가져오기와 채점

실제 실행·판정이 완료된 캡처를 준비한 뒤 각 시스템에 같은 계획을 사용한다.

```sh
node scripts/evaluate-baselines.mjs import \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan.json --capture .local/gemini-capture.json \
  --output .local/gemini-run.json

node scripts/evaluate-baselines.mjs score \
  --catalog .local/agent-catalog-b0602a1.json \
  --set .local/evaluation-set-b0602a1.json \
  --plan .local/baseline-plan.json --run .local/gemini-run.json \
  --details .local/gemini-scores.json --report ../../reports/baseline-gemini-summary.json
```

Langflow도 같은 명령에서 캡처·실행·상세·보고서 파일명을 바꾸어 사용한다. 저장된 결과 확인은 동일한 `score` 명령 뒤에 `--check`를 붙인다. 기존 결과를 바꿀 때는 새 파일명을 사용한다.

## 6. 버전 고정과 지표 해석

계획에는 정답·작성 질의·명세·카탈로그·페르소나·테마 매핑 해시가 함께 들어간다. 요청별로 실제 질의와 맥락의 해시를 고정한다. 읽은 평가셋은 `validateEvaluationSet`으로 재검증하고 계획도 원본 입력에서 재생성해 대조하므로, 해시 문자열만 바꾸거나 정답 조건·질의를 섞은 파일은 받지 않는다. 실행에는 모델 ID, 프롬프트·도구·어댑터 버전, 실행 시각을 기록한다.

집계는 시스템과 문체별로 분리한다. 공개 보고서에는 임의 모델·버전 문자열을 그대로 노출하지 않고 로컬 메타데이터의 `provenanceSha256`, 전체 실행의 `runSha256`을 남긴다. 동일한 `planSha256`·`binding`인지 먼저 확인한 뒤 두 시스템을 비교한다.

- `plannedCases`, `successfulResponses`, `failedCases`, `completionRate`, 오류별 개수는 누락 없는 전체 실행을 설명한다.
- `actionAccuracyAllCases`는 실패 문항을 포함한 전체 요청이 분모다. 응답한 일부 문항만으로 정확도를 높여 보이지 않는다.
- `metrics`의 허용 ID 위반·필수 조건 오판·재현율@5·정밀도@5·테마 일치도·고지율·지연·비용은 **성공적으로 구조화된 응답**의 집계다. 성공 응답이 없으면 `null`이다. 전체 완료율과 함께 읽는다.
- `unexpectedImpossible`은 정답이 `impossible`이 아닌 성공 응답 중 그 동작을 반환한 비율이다. 전체 적용 문항과 실제 응답 문항 수를 따로 기록하며, 실제 응답이 없으면 비율은 `null`이다. 모든 형태의 과도한 거절을 검출하는 지표는 아니다.
- 지연·비용의 측정 대상 수를 따로 기록한다. `null`은 0원·즉시 응답을 뜻하지 않는다.

필수 조건의 판정 규칙과 지표 정의는 [23 문서 5·7절](23-agent-step3-evaluation-set.md)을 그대로 사용한다. 현재 카탈로그의 미확인 값을 근거 없이 `pass`나 `fail`로 승격하지 않는다.

## 7. 남은 검증과 보안 사례

실제 모델 실행기, Gemini·Langflow 두 시스템의 같은 계획에 대한 실측, 사람의 24문항 표본 검수, 작성 질의와 템플릿 문체 비교가 남아 있다. 현재 import 계약이 실제 모든 Langflow 흐름의 출력 형식을 포괄한다는 뜻은 아니며, 계약 밖 출력은 자동 추측하지 않고 오류로 처리한다. 합성 질의의 점수는 실제 관광객 만족도를 대신하지 않는다.

[`agent-security-cases.json`](../data/agent-security-cases.json)의 **14개 보안 사례는 여전히 미실행**이며 집계도 `securityEvaluation: "not_executed"`로 표시한다. SEC-003(조건 삭제), SEC-004(`unknown` 승격), SEC-005(허용 목록 밖 ID)는 공격 입력을 덧붙인 별도 계획을 만들되 원래 정답을 고정하는 확장이 필요하다. 현재 계획에서 질의만 임의 변경하면 해시 검증이 거부하며, 정상 96문항과 공격 사례를 같은 성공률로 섞지 않는다.

권한·테넌트·비밀·SSRF·반복 호출·렌더링 사례에는 모의 호출 횟수, 상태 전후 해시, 합성 canary 유출 여부, 감사 이벤트, 브라우저 요청 등 [22 문서](22-jailbreak-and-prompt-injection-defense.md)의 실제 부작용 계측이 추가로 필요하다. 거절 문구 또는 채점기 자체 검사만으로 보안 방어 성공을 주장하지 않는다.
