# Hugging Face 모델·데이터셋 평가: SynthTRIPs · Qwen3.5-4B · 장소 검색 임베딩

기준일: 2026-11-22 한국 시간(실행 시각은 2026-11-21 UTC, 보고서 파일명도 UTC 날짜). 사용자 요청(2026-11-21): “평가 데이터셋을 Hugging Face에서 받아 에이전트 평가에 쓰기, 후보 공개 모델(Qwen 등)을 한국어 제주 질문으로 직접 평가하기, 임베딩 모델을 골라 장소 검색에 붙이기 — 다”. 사용자 선택: 결과는 이 저장소에 PR로, 모델은 Qwen3-4B 대신 **Qwen3.5-4B**, Hugging Face CDN 호스트 허용. 2.1절(Qwen 고도화)은 2026-11-22 UTC 실행이다.

모두 분석 세션 컨테이너(4 vCPU·15GB RAM·GPU 없음)에서 실행했다. 운영 앱·AWS·Langflow 설정은 바꾸지 않았다. 앱 연결은 앱 저장소의 별도 PR이며 아직 원격 호출을 하지 않았다. `productionReady:false`를 유지한다. 도구 사용법은 [hf-eval README](../services/hf-eval/README.md)에 있다.

## 요약

| 작업 | 결과 | 해석의 한계 |
|---|---|---|
| SynthTRIPs(HF `ashmib/synthTRIPS`, MIT) | 유럽 200개 도시·질의 2,302×3개로 임베딩을 외부 점검. `multilingual-e5-large`가 세 질의 유형 중 두 개에서 1위 | 영어·유럽 도시. 제주 Agent 정답으로 쓰지 않는다 |
| Qwen3.5-4B 로컬(Q4_K_M, CPU) | 96문항: 수락 70·최종 검증 차단 26, 전체 동작 정답 **52/96** (v2 Langflow 67/96, 기존 Gemini 48/96) | 시스템 구성이 다르다(아래 2절). 모델 우열 주장 아님 |
| Qwen 고도화(2.1절) | 시드만 바꾼 새 96문항: 서버 규칙이 동작을 정하고 모델은 장소만 고르면 차단 26→0, 동작 정답 55→**95/96**(겹치지 않는 73문항 42→73). 장소 선택 지표는 비슷하다 | 동작 정답은 규칙과 정답 규칙의 일치에 가깝다. 모델 향상이 아니다 |
| 임베딩 6종 비교 | 전체 카탈로그 nDCG@10: arctic-l 0.599 · e5-large 0.582 · bge-m3 0.578 · e5-small 0.465 · BM25 0.310 | 정답은 규칙으로 만든 필터 일치 집합이며 사용자 관련성이 아니다 |
| 앱 장소 검색 연결 | e5-large 선택. 키워드 후보는 그대로 두고 빈 자리만 의미 후보로 채움. 96문항 템플릿에서는 **키워드 검색과 같은 결과** | 효과는 키워드 해석이 비거나 적은 자유 질의에서만 생기며 이번 자료로는 측정하지 못했다 |

## 1. SynthTRIPs를 평가에 쓰는 방법

데이터셋 카드(MIT)의 내용: 지식베이스 `kb/eu-cities-database.csv`(4,767행, 도시·국가·예산·보행성·대기질·인기·성수기·관심 장소), Gemini 1.5 Pro와 Llama 3.2 90B로 만든 질의 파일 각 2,302개 설정, 질의 생성·검증 프롬프트. 설정은 페르소나 200명 × 난이도(`easy` 600, `medium` 588, `hard` 577, `sustainable` 537)이며 조건 조합은 16종이다. 각 설정에 `query_v`·`query_p0`·`query_p1` 세 질의가 있다. 카드가 세 변형의 차이를 정의하지 않아 이름 그대로 따로 집계했다. 질의 길이 평균은 12~14 단어다.

지식베이스가 유럽 도시라 제주 평가셋을 대체하지 못한다. 이번에는 두 가지로 썼다.

1. **임베딩 외부 점검**: 같은 임베딩 모델로 질의→도시 순위를 매기고, 설정의 `city` 목록(생성 조건을 만족하는 도시, 평균 19.7곳)을 정답으로 채점했다 ([집계](../reports/synthtrips-city-retrieval-20261121.json), Gemini 생성 질의).

   | 시스템 | `query_v` nDCG@10 | `query_p0` | `query_p1` |
   |---|---:|---:|---:|
   | BM25(bigram) | 0.162 | 0.137 | 0.143 |
   | multilingual-e5-small | 0.192 | 0.182 | 0.165 |
   | **multilingual-e5-large** | **0.278** | **0.243** | 0.167 |
   | bge-m3 | 0.228 | 0.202 | **0.169** |
   | snowflake-arctic-embed-l-v2.0 | 0.186 | 0.181 | 0.159 |

   인기도·예산·월 같은 수치형 조건은 임베딩만으로 잘 맞지 않아 점수가 전반적으로 낮다. 제주 결과에서 1위였던 arctic-l이 여기서는 낮아, 한 자료만으로 모델을 고르지 않는 근거가 됐다.

2. **방법론 대조**: 우리 96문항은 이미 SynthTRIPs 방식(조건 설정→근거 집합→질의, [23 문서](23-agent-step3-evaluation-set.md))을 따른다. 데이터셋 프롬프트 중 `filter_groundedness`(질의에 조건이 몇 개 드러나는지 세기)는 우리 질의 규칙 검사와 같은 역할이고, `persona_alignment`(A 비정렬·B 부분·C 정렬·D 불명)는 작성 질의의 사람 검수 기준으로 옮겨 쓸 수 있다. 이 두 판정 프롬프트는 실행하지 않았다(모델 공급자 호출이 필요하고, 사람 검수 24건이 먼저다).

## 2. Qwen3.5-4B 로컬 평가

### 실행 구성

| 항목 | 값 |
|---|---|
| 모델 | `ggml-org/Qwen3.5-4B-GGUF` `Qwen3.5-4B-Q4_K_M.gguf` (리비전 `8d43d97`, sha256 `217002fa…a89c14`, 원본 `Qwen/Qwen3.5-4B`, Apache-2.0 표기) |
| 실행 | llama-cpp-python 0.3.37 CPU, context 8192, 4스레드, temperature 0, 루프백 서버 [`local_llm_server.py`](../services/hf-eval/local_llm_server.py) |
| 생각 모드 | 빈 `<think></think>` 블록으로 끔. 끄지 않으면 200토큰을 생각에 쓰고 JSON을 내지 않았다(사전 측정) |
| 속도(사전 측정) | 프롬프트 처리 약 80 tok/s, 생성 약 10 tok/s |
| 경로 | [`collect-local-llm.mjs`](../services/hf-eval/scripts/collect-local-llm.mjs): v2와 같은 닫힌 템플릿 요청 → `candidatePlaces` 후보 → arctic-l 임베딩 재정렬 상위 12곳 → 필수 조건 판정(`runtimeRequirementEvidence`) → JSON 생성 1회 → v2와 같은 `createRuntimeValidator` |
| 출력 제한 | JSON 스키마→GBNF. 장소 ID는 보여 준 후보만, 조건 종류·고지 주제는 요청된 것만 허용. `schemaVersion`·`requestId`는 수집기가 채운다 |
| 프롬프트 | v2 Langflow 프롬프트의 규칙을 도구 대신 후보 목록 입력으로 옮긴 `local-llm-prompt-v1`. 3건 시험 뒤 고치지 않고 고정했다 |
| 채점 | 기존 `evaluate-baselines.mjs import/score`. 새 `local_llm` 시스템·`local-llm` 구조화 어댑터는 최종 검증을 통과한 출력만 수정 없이 받는다. `score --check` 재현 확인 |

v2 Langflow는 모델이 MCP 도구를 골라 부르는 Agent다. 이번 경로는 검색·조건 판정을 서버가 한 번에 하고 모델은 고르기만 한다. 따라서 결과는 **구성된 시스템 전체**의 값이며 Gemini와 Qwen 모델만 바꾼 비교가 아니다.

### 결과 ([채점 집계](../reports/baseline-local-qwen35-4b-20261121.json) · [수집 진단 집계](../reports/local-qwen35-4b-collection-20261121.json))

| 지표 | Qwen3.5-4B 로컬 | v2 Langflow (Gemini) | 기존 Gemini 비서 |
|---|---:|---:|---:|
| 정상 응답 / 실패 | 70 / 26 | 73 / 23 | 92 / 4 |
| 실패 내역 | 최종 검증 차단 26 | 전송 9·출력 14 | — |
| 전체 동작 정답 | **52/96** (0.542) | 67/96 (0.698) | 48/96 (0.500) |
| 응답 중 동작 정답 | 0.743 | 0.918 | 0.522 |
| 허용 밖 ID | 14/236 (5.9%, 5문항, 모두 정답 후보 범위 밖) | 7/295 (2.4%, 3문항) | 117/333 (35%, 38문항) |
| Recall@5 / Precision@5 | 0.831 / 0.916 | 0.956 / 0.915 | 0.452 / 0.691 |
| 미확인 조건을 pass·fail로 단언 | 0/6 | 0/17 | 25/30 |
| 확인 고지 | 9/22, 주제 0.273 | 17/31, 주제 0.516 | 4/46 |
| 지연 p50 / p95 (수락분) | 26.8초 / 42.0초 (CPU) | 11.9초 / 50.1초 | 9.1초 / 31.8초 |

프롬프트 토큰 p50 947(최대 1,523), 생성 토큰 p50 51(최대 213)이다. 비용은 로컬 CPU라 `null`이다. Gemini 비서 열은 세션 내 모델 주석의 잠정값이며 사람 검수 0/24건이다.

### 실패의 원인

26건 차단은 모두 같은 모양이다: **장소를 하나도 고르지 않고** `cannot_confirm`(22건) 또는 `ask_clarification`(4건)에 확인 주제만 붙였다. 런타임 계약은 `cannot_confirm`이면 확인이 필요한 장소를 함께 내야 한다(`no_unconfirmed_candidate`). 모델은 “확인할 수 없다”를 “고르지 않는다”로 해석했다. 정답 동작별로는 기대 `cannot_confirm` 32건 중 6건만 수락됐고, 기대 `impossible` 8건은 모두 `ask_clarification`으로 답했다.

고치는 방법은 프롬프트에 “cannot_confirm에도 후보 장소를 넣는다”를 명시하거나, 스키마를 동작별로 나눠(`cannot_confirm`→`placeIds` 1개 이상) 문법으로 강제하는 것이다. 같은 96문항으로 고친 뒤 다시 재면 평가셋에 맞춘 조정이 되므로 이번에는 하지 않았다. 고친 버전은 프롬프트 버전을 올리고, 별도 문항(작성 질의나 새 시드)으로 먼저 확인한다.

[실패 분해](../reports/local-qwen35-4b-failure-breakdown-20261121.json)로 보면 틀린 44건 대부분이 **서버가 이미 아는 사실로 정할 수 있는 동작**이었다. 기대 `ask_clarification` 8건은 요청 변환 감사가 이미 날짜 충돌을 보고했고, 기대 `impossible` 8건 중 7건은 필터·제외 뒤 후보가 0곳이었다. 반면 검색은 대체로 맞았다. 장소가 필요한 79문항 중 75문항에서 보여 준 12곳 안에 정답 후보가 있었고, 보여 준 후보 중 정답 비율은 평균 0.733이었다.

### 2.1 고도화 실험: 동작은 서버 규칙, 모델은 장소 선택만 (2026-11-22)

사용자 요청(2026-11-22): “성능 비교 후 고도화 방향성이 있는지 우선 파악” → “1~2 진행하고 문서에도 기록”. 1은 아래 규칙 동작 모드 구현, 2는 새 96문항 재평가다.

**구현(`collect-local-llm.mjs --mode rule-action`, 프롬프트 버전 `local-llm-prompt-v2-rule-action`).**

1. 서버가 실행 시점 정보만으로 동작을 먼저 정한다.
   - 요청 변환 감사에 날짜 충돌이 있으면 `ask_clarification`로 정한다.
   - 필터·제외 뒤 후보가 없거나, 모든 후보가 확인된 `fail`이면 `impossible`로 정한다.
   - 이 두 경우는 모델을 부르지 않는다.
2. 나머지 경우에는 모델이 보여 준 12곳 중 1~5곳의 ID만 고른다. 고를 수 있는 ID는 문법(GBNF `enum`)으로 제한한다. 선호와 걷기 최소화 같은 문맥은 프롬프트로만 전달한다.
3. 고른 장소에 대해 서버가 조건별 근거(`runtimeRequirementEvidence`)로 판정을 만든다.
   - 모든 필수 조건이 `pass`이면 `recommend`이다.
   - 아니면 `cannot_confirm`과 확인 고지를 붙이고, `unknown` 조건에서 확인 주제를 만든다.
4. 결과는 v1과 같은 최종 검증(`createRuntimeValidator`)을 통과해야 수락된다.

정답 파일은 어느 단계에서도 읽지 않는다. 이 규칙은 원래 96문항의 실패를 본 뒤에 설계했다.

**새 96문항(held-out).**

- 공개 명세(`data/evaluation-spec.json`)에서 `seed`만 바꾼 명세(`jeju-evalset-heldout-20261122`)로 기존 빌더가 새 평가셋을 만들었다(`services/hf-eval/scripts/heldout.mjs`). 이 도구는 시드 외의 차이를 거부한다.
- 정답은 규칙으로만 계산했다(gold sha256 `8c2d8299…bd9b`, plan sha256 `cfc257de…f0f`).
- 동작 분포는 원래 세트와 같은 48·32·8·8이다.
- 원래 세트와 템플릿 문장이 같은 문항이 23개 있어, 겹치지 않는 73문항도 따로 셌다.
- oracle 자체 검사는 동작 정확도 1.0이다.
- 질의와 정답은 `.local`에만 있다.
- 새 세트로 수집한 뒤에는 규칙과 프롬프트를 바꾸지 않았다.

**결과** ([v2 채점](../reports/baseline-local-qwen35-4b-v2-heldout-20261122.json) · [v1 채점](../reports/baseline-local-qwen35-4b-v1-heldout-20261122.json) · 실패 분해 [v2](../reports/local-qwen35-4b-v2-heldout-failure-breakdown-20261122.json)·[v1](../reports/local-qwen35-4b-v1-heldout-failure-breakdown-20261122.json) · 수집 집계 [v2](../reports/local-qwen35-4b-v2-heldout-collection-20261122.json)·[v1](../reports/local-qwen35-4b-v1-heldout-collection-20261122.json))

같은 모델·검색·최종 검증에서 동작을 정하는 방식만 다르다.

| 지표 (새 96문항) | v1 모델이 동작까지 결정 | v2 서버 규칙이 동작 결정 |
|---|---:|---:|
| 수락 / 최종 검증 차단 | 70 / 26 | **96 / 0** |
| 전체 동작 정답 | 55/96 | **95/96** |
| 겹치지 않는 73문항 동작 정답 | 42/73 | **73/73** |
| 기대 동작별 (recommend·cannot_confirm·ask·impossible) | 45/48 · 6/32 · 4/8 · 0/8 | 48/48 · 32/32 · 8/8 · 7/8 |
| 모델 호출 문항 | 96 | 81 (나머지 15건은 서버가 0.1초 안에 결정) |
| 허용 밖 ID (전체) | 12/269 (4.5%) | 31/386 (8.0%, 10문항: 후보 범위 밖 24·걷기 최소화에 탐방로 7) |
| 미확인 조건을 pass·fail로 단언 | 0/6 | 0/32 |
| 확인 고지 / 주제 범위 | 8/22 · 0.273 | 32/48 · 0.604 |
| 지연 p50 / p95 (모델 호출분, CPU) | 30.9초 / 59.7초 (수락분) | 23.8초 / 27.5초 |

v1은 새 세트에서도 처음 세트와 같은 방식으로 실패했다. 차단 26건 중 22건이 `no_unconfirmed_candidate`, 26건 모두 `unsupported_confirmation_topic`이었다. 새 문항에서도 같은 계약 오해가 재현됐다는 뜻이다.

**모델이 실제로 맡은 부분(장소 선택)은 거의 같다.** 두 방식 모두 장소를 낸 공통 문항만 비교하면 다음과 같다.

| 지표 (공통 문항) | v1 | v2 |
|---|---:|---:|
| Recall@5 (54문항) | 0.880 | 0.917 |
| Precision@5 (51문항) | 0.929 | 0.910 |
| 테마 일치 (51문항) | 0.692 | 0.689 |
| 허용 밖 ID (51문항) | 12/249 | 14/249 |

**해석.**

- 개선은 전부 동작 결정을 서버로 옮긴 데서 나왔다. 서버 규칙은 정답 규칙과 같은 카탈로그 근거와 요청 감사를 보므로, 동작 정답 95/96은 **규칙끼리 일치한다는 뜻에 가깝고 모델 능력의 향상이 아니다**.
- 그래도 “4B 모델에게 동작 판단을 맡기지 말고 장소 선택만 맡긴다”는 방향은 새 문항에서도 유지됐다. 차단 26→0, 지연 분산 감소, 모델 호출 15% 감소가 근거다.
- 남은 오류는 서버 규칙과 정답 규칙의 차이 두 가지다.
  - 걷기 최소화 요청에서 탐방로·자전거길을 빼지 않는다. 그래서 `impossible`이어야 할 1건을 `recommend`로 냈고, 허용 밖 ID 7개도 이 때문이다.
  - 확인 주제를 필수 조건에서만 만든다. 그래서 문맥 조건(걷기 거리 8·혼잡 3·이동 수단 3·동행 2) 16건에서 `recommend`에 확인 고지가 빠졌다.
  - 둘 다 이번 결과를 본 뒤에 고치면 새 세트에도 맞춘 조정이 된다. 고친다면 다른 시드로 다시 확인해야 한다.
  - 2026-11-28: 앞의 것(걷기 최소화 경로 제외)을 운영 Gateway MCP의 범위 규칙으로 구현했다. 새 시드의 세 번째 세트(`jeju-evalset-heldout-20261128`, gold `2e9a935f…fd98`)를 만들어 모델 없는 기준선으로만 쟀다([35 문서 6절](35-gateway-model-comparison.md#6-걷기-최소화-경로-규칙2026-11-28), 구현·작업 환경 확인, AWS 미적용). 그 세트에서 답이 바뀐 문항은 앞 세트와 같은 요청뿐이라, 새 문항에서의 효과는 아직 확인하지 못했다. 이 절의 로컬 수집기(`collect-local-llm.mjs --mode rule-action`)는 후보를 따로 만들므로 바뀌지 않았고, Qwen은 다시 재지 않았다. 문맥 확인 주제는 그대로 남아 있다.
- 허용 밖 ID 비율이 늘어난 것은 v2가 장소를 내는 문항이 많아진 영향이 크다. 공통 문항에서는 12→14개로 비슷하다.
- 참고로 원래 96문항에도 v2를 돌렸다([채점](../reports/baseline-local-qwen35-4b-v2-original-20261122.json) · [실패 분해](../reports/local-qwen35-4b-v2-original-failure-breakdown-20261122.json) · [수집](../reports/local-qwen35-4b-v2-original-collection-20261122.json)). 결과는 95/96이고, v1은 52/96이었다. 규칙 설계에 쓴 세트라 이 수치는 판단 근거로 쓰지 않는다.
- v2 Langflow(67/96)나 Gemini와의 우열은 주장하지 않는다. 시스템 구성이 다르고, 운영 Gateway는 이미 서버 근거로 조건 판정을 재구성하는 별도 경로를 갖고 있다(32 문서).
- 운영 앱과 Langflow 흐름에는 아무것도 적용하지 않았다. `productionReady:false`를 유지한다.

**고도화 방향(제안, 미검증).**

1. 동작은 서버 규칙으로 정하고 모델은 장소 선택만 하는 구조를 유지한다.
2. 위 두 규칙 차이(걷기 최소화 경로 제외, 문맥 확인 주제)를 반영한 뒤 **새 시드의 세 번째 세트**로 확인한다. 2026-11-28 걷기 최소화 경로 제외만 운영 Gateway MCP 코드에 구현했다(구현, AWS 미적용). 세 번째 세트의 모델 없는 기준선으로 쟀지만 새 문항에서의 효과는 아직 확인하지 못했다(위 남은 오류의 2026-11-28 항목).
3. 그다음 작성 질의·사람 검수로 장소 선택 품질을 본다. 템플릿 질의의 Precision@5는 이미 0.9 안팎이라 이 세트로는 모델 차이가 잘 드러나지 않는다.
4. 운영 Agent에 같은 구조를 적용할지는 별도 결정이다.

## 3. 장소 검색 임베딩 비교

[`compare-embeddings.mjs`](../services/hf-eval/scripts/compare-embeddings.mjs), [집계](../reports/embedding-retrieval-comparison-20261121.json). 장소 문서는 이름·종류·분류·권역·출처 메모(`place-text-v1`)이며 테마 가중치와 정답은 넣지 않았다. 질의는 96개 템플릿, 정답은 `gold.relevantIds`가 있는 87문항이다. transformers.js 4.3.2 ONNX q8, CPU.

| 시스템 | 전체 981곳 P@10 | nDCG@10 | MRR | R@50 | 필터 후보 안 nDCG@10 | 질의 1건 | 크기 | 라이선스 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BM25(bigram) | 0.270 | 0.310 | 0.395 | 0.538 | 0.799 | — | — | — |
| multilingual-e5-small | 0.377 | 0.465 | 0.549 | 0.636 | 0.843 | 3ms | 130MB | MIT |
| multilingual-e5-base | 0.347 | 0.394 | 0.494 | 0.546 | 0.805 | 6ms | 283MB | MIT |
| multilingual-e5-large | 0.508 | 0.582 | 0.657 | 0.738 | 0.848 | 18ms | 553MB | MIT |
| bge-m3 | 0.478 | 0.578 | **0.689** | 0.699 | 0.842 | 17ms | 560MB | MIT |
| snowflake-arctic-embed-l-v2.0 | 0.495 | **0.599** | 0.676 | **0.754** | **0.851** | 21ms | 560MB | Apache-2.0 |
| Qwen3-Embedding-0.6B | 0.292 | 0.349 | 0.469 | 0.550 | 0.798 | 172ms | 597MB | Apache-2.0 |

- 전체 카탈로그에서는 대형 모델 셋(e5-large·bge-m3·arctic-l)이 BM25의 약 2배다. 닫힌 문법이 권역·분류·테마로 후보를 이미 좁힌 뒤에는 차이가 작다(0.80→0.85).
- Qwen3-Embedding 수치는 결론에서 뺀다. 마지막 토큰 풀링에 필요한 종료 토큰·지시문 형식을 공식 사용법대로 맞췄는지 확인하지 못했다.
- Qwen 수집 경로의 재정렬에는 제주 점수 1위인 arctic-l을 썼다.

### 앱에 붙일 모델: `intfloat/multilingual-e5-large`

- 제주 점수가 1위와 근소한 차이이고, SynthTRIPs 외부 점검에서는 1위다.
- 모델 페이지의 공개 API(`inferenceProviderMapping`)에서 Hugging Face Inference Providers의 `hf-inference`가 `feature-extraction`으로 live다. arctic-l·bge-m3는 `sentence-similarity` 작업으로만 표시돼 질의 벡터를 받는 경로가 다르다.
- 모델을 Vercel 함수 안에서 직접 돌리는 방법은 쓰지 않았다. `onnxruntime-node`만 548MB라 함수 크기 한도 250MB를 넘는다.

## 4. 앱 장소 검색 연결 (앱 저장소 PR)

- 장소 벡터: 앱 공개 장소 981곳을 같은 문장 규칙(`app-place-text-v1`)으로 만들고, 같은 가중치의 fp32 ONNX 변환본(`Xenova/multilingual-e5-large@00fc3ae`)으로 계산해 int8로 저장한다(1.36MB).
- 질의 벡터: 요청 때 `HF_TOKEN`으로 `hf-inference`에 한 번 묻는다. 3초 제한·하루 한도가 있고, 실패하면 의미 점수 없이 기존 키워드·Gemini 검색 그대로다. 원격 응답과 로컬 fp32 벡터의 일치는 토큰이 없어 아직 확인하지 않았다.
- 섞는 방식: 키워드 점수 후보(`rank`)를 순서 그대로 모두 두고, n곳(Gemini 후보 60곳·규칙 검색 20곳)에 못 미친 자리만 같은 조건(분류·제외어·숙소 규칙·요청 권역)을 통과한 장소로 채운다. 키워드 후보가 없으면 의미 순으로 고른다.

[`compare-app-search.mts`](../services/hf-eval/scripts/compare-app-search.mts)로 87문항을 쟀다. Gemini 해석 단계 대신 앱의 규칙 해석(`ruleIntent`)을 썼다 ([집계](../reports/app-place-search-blend-20261121.json)).

| 방식 | 규칙 검색 P@10 | MRR | Gemini 후보 60곳 재현율 |
|---|---:|---:|---:|
| 키워드만 | 0.545 | 0.644 | 0.816 |
| 의미 후보로 빈 자리 채움(채택) | 0.545 | 0.644 | 0.816 |
| (탐색) 의미 후보 15곳이 키워드 후보를 밀어냄 | — | — | 0.793 |
| (탐색) 빈 자리 채움, 분류 조건 없이 | — | — | 0.822 |

- 처음 구현은 키워드 후보 일부를 의미 후보로 바꿨고 재현율이 0.816→0.793으로 내려갔다. 그래서 밀어내지 않는 방식으로 바꿨다. 이 선택은 이 평가셋을 본 뒤 한 것이다.
- 채택한 방식이 키워드만과 같은 값인 이유: 분류·제외어 조건을 지키면 `rank`가 이미 조건에 맞는 장소를 전부 돌려주고, 템플릿 질의는 규칙 해석이 비는 경우가 없다(빈 결과 0건).
- 분류 조건을 풀면 +0.006이 나왔지만 규칙 검색에서 다른 분류가 섞일 위험이 있어 쓰지 않았다.
- 따라서 이 변경의 효과는 **자유 질의(규칙·Gemini 해석이 비거나 후보가 적을 때)에서만** 생길 수 있다. 템플릿에서는 해가 없음을 확인했고, 자유 질의 효과는 4.2절에서 따로 쟀다.

### 4.1 운영 연결 관측 (2026-11-22 KST)

사용자가 앱 [PR #58](https://github.com/OREUM-IT/jeju-tour/pull/58)과 실패 이유 표시 [PR #60](https://github.com/OREUM-IT/jeju-tour/pull/60)을 병합하고 Vercel에 `HF_TOKEN`을 등록했다. 아래는 작성자가 운영 `jeju-tour-six.vercel.app`의 공개 API로 관측한 것이다.

| 시점 | 관측 | 해석 |
|---|---|---|
| #58 배포 전 | Preview 배포가 빌드 전에 실패 | `vercel.json` `includeFiles` 256자 제한 초과(266자). 벡터 파일을 빌드 때 `dist/`로 복사해 해결 |
| #58 배포 뒤, 결제 설정 전 | 상태 `enabled:true`. 검색 3건 모두 의미 점수 없음(`calls:3, errors:3`), 검색 2.2초 안에 종료 | 시간 초과가 아닌 즉시 거절. 이유는 기록되지 않았다 |
| #60 배포 뒤 | 검색 1건에서 `lastError.reason:"http_402"` | Hugging Face Inference Providers 결제 크레딧 부족 |
| 사용자가 Hugging Face 결제 설정 | 새 검색 2건 `semantic:true`, 새 실패 없음(`calls:3, errors:1`, 실패 1건은 결제 전) | 원격 질의 벡터 호출 성공 |

결제 뒤 "노을 보며 조용히 쉬기 좋은 곳" 검색 결과는 해안 산책로·해안도로·오름·해변 6곳이었다. 결과 중 어느 장소가 의미 후보로 채워졌는지는 응답에 표시되지 않는다. 원격 질의 벡터와 로컬 fp32 장소 벡터의 수치 일치는 이 작업 환경에서 `router.huggingface.co`가 막혀 직접 비교하지 못했다. 결과가 질의와 맞는다는 것은 간접 근거일 뿐이다. 호출 비용은 사용자 결제 화면에서만 확인할 수 있으며 미측정이다.

### 4.2 자유 질의 점검

[`compare-app-search-free.mts`](../services/hf-eval/scripts/compare-app-search-free.mts), [집계](../reports/app-place-search-free-queries-20261122.json).

- 질의: 템플릿 문법을 쓰지 않은 자연스러운 질의 35개를 작성자(Claude)가 실행 전에 썼다(`free-queries-v1`, `.local`). 실제 사용자 질의가 아니다.
- 정답: 질의마다 실행 전에 정한 규칙으로 카탈로그에서 계산했다. 분류가 일치하거나 테마 가중치가 0.5 이상이면 관련이고, 권역이 있으면 권역도 맞아야 하며, 숙소는 제외한다. 모델이 장소를 고르지 않았다.
- 실행: Gemini 해석 대신 `ruleIntent`를 쓰고 Gemini 고르기 단계는 실행하지 않았다. 질의 벡터는 로컬 fp32 ONNX다.

| 범위 | 방식 | 규칙 검색 P@10 | MRR | 빈 결과 | Gemini 후보 중 관련 수 / 후보 수 |
|---|---|---:|---:|---:|---:|
| 전체 35 | 키워드만 | 0.786 | 0.844 | 1건 | 40.1 / 46.1 |
| 전체 35 | 빈 자리 채움 | **0.831** | **0.865** | 0건 | 43.3 / 54.3 |
| 키워드 후보가 60곳 미만인 12 | 키워드만 | 0.592 | 0.628 | 1건 | 18.7 / 19.5 |
| 키워드 후보가 60곳 미만인 12 | 빈 자리 채움 | **0.725** | **0.690** | 0건 | 27.9 / 43.5 |

- 키워드 후보가 이미 충분한 23개 질의는 두 방식이 같다. 차이는 키워드 해석이 적게 잡히는 12개에서만 난다. 이때 관련 후보는 18.7곳에서 27.9곳으로 늘고, 결과가 비던 1건도 채워졌다.
- 대신 후보 중 관련 비율은 96%에서 64%로 낮아진다. 운영에서는 Gemini 고르기 단계가 이 후보 중에서 다시 고르므로 비율 하락의 실제 영향은 미측정이다.
- 질의와 규칙을 작성자가 정했으므로 사용자 질의 분포나 사용자 만족을 대표하지 않는다. 실제 검색 기록을 쓰려면 개인정보 경계를 먼저 정해야 한다.

## 5. 재현

모두 `services/jeju-mcp/.local`에 카탈로그·평가셋·계획이 있을 때다(HF 1 단계에서 `b0602a1` 기준 복원, gold sha256 `b9821945…1217d`, plan sha256 `6da7f074…eb0d4`).

```bash
NODE_USE_ENV_PROXY=1 node services/hf-eval/scripts/compare-embeddings.mjs --catalog services/jeju-mcp/.local/agent-catalog-b0602a1.json --set services/jeju-mcp/.local/evaluation-set-b0602a1.json --details services/jeju-mcp/.local/embedding-details.json --report /tmp/embedding.json --vectors-dir services/jeju-mcp/.local/embeddings
```

```bash
~/hf-venv/bin/python -I services/hf-eval/local_llm_server.py --model <Qwen3.5-4B-Q4_K_M.gguf> --model-id "ggml-org/Qwen3.5-4B-GGUF:Q4_K_M"
```

```bash
NO_PROXY=127.0.0.1,localhost node services/hf-eval/scripts/collect-local-llm.mjs --plan services/jeju-mcp/.local/baseline-plan.json --catalog services/jeju-mcp/.local/agent-catalog-b0602a1.json --vectors services/jeju-mcp/.local/embeddings/snowflake-arctic-embed-l-v2.0.json --embedding snowflake-arctic-embed-l-v2.0 --model-id "ggml-org/Qwen3.5-4B-GGUF:Q4_K_M" --capture services/jeju-mcp/.local/capture.json --diagnostics services/jeju-mcp/.local/diag.json
```

이후 `evaluate-baselines.mjs import`·`score`(기존 절차)와 `summarize-local-llm.mjs`로 집계한다. 실패 분해는 `analyze-local-llm-failures.mjs`로 만든다.

2.1절의 새 세트는 다음 순서로 만들고 잰다.

1. 명세를 만든다: `node services/hf-eval/scripts/heldout.mjs spec --seed jeju-evalset-heldout-20261122 --output .local/evaluation-spec-heldout-1.json`
2. 같은 도구의 `build --spec …`로 평가셋을 만든다(인자는 `build-evaluation-set.mjs`와 같다).
3. `eval --spec … prepare|self-check|import|score`로 계획 작성·자체 검사·가져오기·채점을 한다(인자는 `evaluate-baselines.mjs`와 같다).
4. 수집은 위 명령에 `--mode rule-action`을 더한다. 기본값은 `model-action`(v1)이다.

테스트: `services/jeju-mcp` 223개(새 `local-llm-import` 2개 포함), `services/hf-eval` 6개(규칙 동작 1개 추가), 앱 `scripts/test-place-embeddings.ts` 5개.

## 6. 남은 일

- Qwen: 2.1절의 규칙 동작 모드로 새 96문항에서 차단 0·동작 95/96을 확인했다. 남은 규칙 차이 두 가지(걷기 최소화 경로 제외, 문맥 확인 주제)는 고친 뒤 새 시드의 세 번째 세트로 확인한다. 걷기 최소화 경로 제외는 2026-11-28 운영 Gateway MCP 코드에 구현했다(구현, AWS 미적용. 로컬 수집기는 그대로다). 세 번째 세트의 모델 없는 기준선으로는 새 문항에서의 효과를 재지 못했다([35 문서 6절](35-gateway-model-comparison.md#6-걷기-최소화-경로-규칙2026-11-28)). 장소 선택 품질은 작성 질의·사람 검수가 필요하다. 사람 검수·합성 보안 14개는 계속 미실행이다.
- 임베딩: 원격 질의 벡터와 로컬 fp32 장소 벡터의 수치 일치 확인(`router.huggingface.co` 접근 가능한 환경에서 같은 문장 1~2개 비교). 의미 후보가 실제 결과에 들어간 비율을 보려면 응답에 출처 표시가 필요하다. 호출 비용은 사용자 결제 화면에서 며칠 뒤 확인.
- SynthTRIPs: persona alignment 기준을 작성 질의 사람 검수표로 옮길지 결정.
