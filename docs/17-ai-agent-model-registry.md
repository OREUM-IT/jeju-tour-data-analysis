# AI Agent 모델 전체 현황과 다음 검증 절차

기준일: 2026-11-28 한국 시간(규칙 동작 GPT 클라이언트의 토큰 기록 수정 구현, AWS 미적용, [32 문서](32-agent-gateway-tunnel.md#토큰-기록-수정stream_usage-2026-11-28)). 이전 기준일 2026-11-27(운영 Gateway에서 GPT 안 `rule-action`과 Gemini 안 `vertex-8-call`을 같은 새 96문항으로 비교한 채점 추가, 사용자 제공 출력, [35 문서 5절](35-gateway-model-comparison.md#5-두-안-비교2026-11-27-사용자-제공-출력)), 2026-11-25(Gateway 규칙 동작 흐름 gpt-5.4-mini의 AWS 적용·실행 관측 추가, [32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)), 2026-11-22(Qwen 규칙 동작 모드·새 96문항 재평가 추가, 2026-11-21 Qwen3.5-4B 로컬 평가·임베딩 선택 추가, [33 문서](33-huggingface-models-evaluation.md)), 그 전 2026-11-19. v2 평가 보고서의 확인일은 2026-11-19 UTC이며 개별 실행의 원래 UTC 시각은 보고서에 보존한다. **기존 Gemini·Langflow v1 기준선 192시도 기록을 보존하고, 같은 981곳을 사용하는 Langflow v2 평가 96시도를 별도로 완료했다. v2는 응답 73건·실패 23건이며 전체 동작 정답은 67/96이다.** Gemini 자유 문장의 모델 주석은 사람 0/24건 검토 상태의 잠정 결과다. v2의 설정·기록 모델은 `gemini-3.8-flash`이며 공급자 응답의 독립 모델 식별과 구분한다. 앱 OpenAI 경로의 실제 호출은 미검증이고, Gateway 규칙 동작 흐름의 gpt-5.4-mini는 2026-11-25 사용자 제공 추적 집계로 실행을 관측했으며 2026-11-27 같은 96문항 운영 Gateway 비교로 규칙 채점했고 같은 날 AWS에 다시 적용했다(사용자 제공 출력. 공급자 응답의 모델 식별·토큰·사람 검수 미확인). 이 비교는 흐름 구조와 모델이 함께 다른 운영 구성 비교이며 모델 우열이 아니다. Claude는 미연동, Ollama 모델은 미실행이다.

이 문서와 [머신 판독용 목록](../data/ai-agent-models.json)은 다른 Claude Code 세션이 현재 근거와 미완료 항목을 이어받기 위한 기록이다. 모델 설치·교체·API 호출·운영 배포를 자동 실행하는 설정 파일이 아니다. `Update all AI AGENT MODEL` 요청에 따라 사용 중인 경로·후보·미선정 역할을 함께 갱신했으며, 모델을 모두 다운로드하거나 운영 기본값을 일괄 변경하지 않았다.

## 1. 기록을 읽는 기준

| 근거 수준 | 확인한 것 | 이 근거만으로 주장할 수 없는 것 |
|---|---|---|
| v2 런타임 설정·기록 관측 | 설정과 저장된 모델 span 255개, MCP 호출 180개, 최종 검증 메시지 | 공급자 응답의 독립 모델 식별·저장된 `ok` 상태만으로 최종 성공 판정 |
| 실제 추론 응답 관측 | 요청 응답·반환 모델 메타데이터, Langflow의 실제 도구 호출·구조화 응답 | 공급자 전체 모델 목록·계정 과금 명세·평균 지연·제품 품질 |
| 분석 저장소 직접 확인 | 설정·스크립트·프로토타입·MCP 구현 파일 | 외부 서버 접속·운영 배포·실제 추론 성공 |
| 원본 앱 소스 확인 | `OREUM-IT/jeju-tour`의 고정 커밋 `b0602a175c1d08959b7187da2b29ac450b52e0ba`에 존재하는 호출 경로·기본 문자열 | 현재 Vercel 배포 커밋·환경변수 override·공급자 모델 가용성 |
| 공식 소스 검토 | 공급자 문서의 언어·라이선스·API·형식 지원 | 사용자 장비 메모리·속도·한국어 과제 정확도 |
| 사용자 보고 | 기존 OpenAI 키 설정·재배포 완료 보고 | 계정 과금·실제 API 응답·모든 호출 경로의 정상 동작 |
| 미검증·미선정 | 아직 실행하지 않았거나 결정하지 않은 항목 | 실패했다거나 기술적으로 불가능하다는 판정 |

JSON의 `key`는 레지스트리 내부 참조다. 공급자의 실제 모델 ID가 아니다. 실제 실행 모델은 `runtimeResolvedModelId`, 원본 코드 기본값은 `sourceDefaultModelId`, 검토 중인 Ollama 태그는 `proposedOllamaTag`에 분리했다. 모르는 실제 모델 ID·digest·하드웨어는 `null`이다. 내부 `key`나 예시 태그를 API 호출 인자로 자동 변환하지 않는다.

## 2. 현재 모델·엔진 목록

| 구성 | 역할·확인한 이름 | 현재 상태 | 다음 검증 |
|---|---|---|---|
| Gemini / Vertex AI 비서 | `GEMINI_ASSISTANT_MODEL`, 코드 기본·실제 반환 `gemini-3.8-flash` | 실제 응답 수집·정규화·잠정 채점 완료. [최종 집계](../reports/baseline-gemini-live-20261119.json); 도구 버전 `jeju-tour-e07a93e`는 수집기 지정 | 사람 24건 검토, 비교 조건 정렬, 사용량·비용 확인 |
| Gemini / Vertex AI 장소 검색·대체 | `GEMINI_MODEL`, 코드 기본·대체 응답 관측 `gemini-3.5-flash-lite` | 기존 비서의 3.8+3.5-lite 혼합 대체 실행 관측. 독립 장소 검색 API는 미검증 | 실패로 보존한 혼합 대체 결과의 원인과 독립 경로·프로젝트·리전 확인 |
| Langflow / Vertex AI Agent | Langflow 1.13.3, v1 반환·v2 설정/기록 `gemini-3.8-flash` | [v1 기준선](../reports/baseline-langflow-live-20261119.json) 보존; [v2 평가](../reports/baseline-langflow-v2-live-20261119.json) 96시도 완료, 응답 73·실패 23. 2026-11-27 AWS 운영 Gateway의 `vertex-8-call` 흐름(v2에서 파생, 모델 8회)으로 새 96문항 비교를 채점했다(사용자 제공 출력, [35 문서 5절](35-gateway-model-comparison.md#5-두-안-비교2026-11-27-사용자-제공-출력)): 수락 84·차단 4·Agent 오류 8(다시 보내지 않음), 전체 동작 정답 77/96, 답한 문항 P@5 0.928·R@5 0.956, 허용 안 된 ID 4/346, 수집기 p50 18.8초·p95 60.6초(결과를 본 88줄). GPT 안과의 운영 구성 비교이며 모델 우열이 아니다(답한 문항이 달라 분모가 다르고 같은 문항끼리 짝지은 비교는 없다). 같은 날 GPT 안을 다시 적용해 이 흐름은 지금 AWS에 적용돼 있지 않다(사용자 제공 출력) | 검색 1회→일괄 제약 검사→최종 JSON 경로 설계·구현·검증, 후보 계약·고지 정렬. 이 안을 운영안으로 고르면 Agent 오류와 느린 응답 줄이기(Agent 오류 8건을 넣은 Langflow 실행 p95는 69.5초). 같은 문항끼리 짝지은 비교·사람 검수 |
| OpenAI | `OPENAI_MODEL`, 코드 기본 `gpt-5.4-mini` | Responses+웹 검색, Chat Completions 일정 설명 코드 존재. 키 설정·재배포는 사용자 보고; 실제 호출 미검증 | 두 API 경로를 각각 확인하고 반환 모델·오류·사용량 기록 |
| Claude / Anthropic API | 정확한 모델 ID 미선정 | 공급자 어댑터 설계만 있음. 관광 앱 미연동 | API 계정의 사용 가능 모델·데이터 정책·비용 확인 후 어댑터 구현 |
| Qwen3-4B / Ollama | 한국어 제품 평가 우선 후보. `qwen3:4b`는 확인 대기 태그 | 공식 언어·라이선스·실행 문서 검토. 모델 적재·한국어 평가 미수행 | 태그가 가리키는 정확한 변형·양자화·digest 확인 후 공통 평가 |
| gpt-5.4-mini / OpenAI (Gateway 규칙 동작 흐름) | 사용자 선택(2026-11-22): 운영 설문 Agent의 장소 선택만 맡김. 동작·판정·확인 안내는 MCP가 조립 | 흐름 빌드·MCP 조립·적용/되돌리기 helper 구현, 단위 테스트·981곳 오프라인 조립·네트워크 없는 클라이언트 생성 확인. 2026-11-25 사용자 AWS 적용 성공(`agent-rule-action-applied`, 메시지 저장 끔 유지)([32 문서](32-agent-gateway-tunnel.md#aws-적용-결과사용자-제공-출력-2026-11-25)). 같은 날 도구 이름 문제로 실제 호출 전에 `--rollback`했고, [PR #37](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/37)(병합됨)이 도구 이름을 밑줄로 고쳤다([32 문서](32-agent-gateway-tunnel.md#도구-이름-수정과-되돌림2026-11-25)). 재적용 뒤 첫 실제 호출 3건이 OpenAI 400(`param:reasoning_effort`)으로 실패해 PR #41이 `reasoning_effort="none"`으로 고쳤다([32 문서](32-agent-gateway-tunnel.md#openai-400-reasoning_effort-수정2026-11-25), 다른 세션 기록). 2026-11-25 사용자 제공 지연·토큰 SQL에서 AWS 실제 실행을 관측했다: 11-25 실행 100건(운영 Gateway 경로, 대부분 [35 문서](35-gateway-model-comparison.md) 비교 수집으로 추정), 수락 82·차단 15·Agent 오류 3(위 400과 수가 같음), 실행 p50 2146 ms, 모델 호출 235회(실패 3) 모두 토큰 기록 없음([32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)). 기록된 모델 이름은 Langflow가 요청 인자에서 읽은 값이며 공급자 응답의 식별은 아니다. 2026-11-25 20:09:59 KST 흐름 변경 뒤 AWS에 저장된 흐름은 Gemini 8회 빌드였다(같은 날 사용자 제공 `apply-agent-call-budget.sh --verify-only` 출력, 35 문서 비교의 Gemini 차례로 보임. 그 절차의 6단계가 GPT 재적용). 2026-11-27 다시 적용하기 전까지 이 빌드는 AWS에 적용돼 있지 않았다. 2026-11-27 같은 새 96문항 운영 Gateway 비교를 채점했다(사용자 제공 출력, [35 문서 5절](35-gateway-model-comparison.md#5-두-안-비교2026-11-27-사용자-제공-출력)): 수락 81·차단 15·Agent 오류 0, 전체 동작 정답 72/96, 답한 문항 P@5 0.842·R@5 0.897, 허용 안 된 ID 15/349(그중 걷기 최소화에 경로 10), 수집기 p50 4.3초·p95 6.4초. Gemini 안과의 운영 구성 비교이며 모델 우열이 아니다(답한 문항이 달라 분모가 다르고 같은 문항끼리 짝지은 비교는 없다. 되묻기 8문항은 운영 조립기가 만들지 않아 맞힐 수 없다). 사람 검수·토큰 미확인. 채점 뒤 같은 날 사용자가 이 빌드를 다시 적용했다(사용자 제공 출력, `agent-rule-action-verified`, 메시지 저장 끔, 모델 미실행, [32 문서](32-agent-gateway-tunnel.md#비교-뒤-다시-적용2026-11-27-사용자-제공-출력)). 웹 추천은 지금 이 빌드로 동작한다 | 차단 15건과 걷기 최소화 경로 제외 규칙 점검(바꾸면 새 시드의 세 번째 세트로 확인). 같은 문항끼리 짝지은 비교·사람 검수. 토큰 기록: 클라이언트에 `stream_usage=True`를 더한 빌드 고정은 2026-11-28 구현(AWS 미적용, [32 문서](32-agent-gateway-tunnel.md#토큰-기록-수정stream_usage-2026-11-28)), AWS 재적용 뒤 지연·토큰 SQL로 확인. 2026-11-27 재적용으로 옮겨진 경계 뒤 로그인 웹 추천이 최종 검증까지 가면 [적용 뒤 확인 SQL](32-agent-gateway-tunnel.md#적용-뒤-확인-sql)로 이 빌드의 저장 끔 직접 판정. 로그인 웹 추천 표시 확인과, 재적용 뒤 실제 웹 실행 20건 이상의 `model`별 차단·Agent 오류 비율([32 문서 되돌리기 기준](32-agent-gateway-tunnel.md#확인과-되돌리기-1). 지연·토큰 SQL은 웹·합성 요청을 나누지 못하므로 비교 수집 날짜는 빼고 본다). 비용은 토큰 기록 뒤 |
| Qwen3.5-4B / llama-cpp-python (로컬 CPU) | 사용자가 Qwen3-4B 대신 선택(2026-11-21). `ggml-org/Qwen3.5-4B-GGUF:Q4_K_M`, digest `sha256:217002fa…a89c14`, 생각 모드 끔 | 고정 단일 경로(서버 검색·조건 판정→JSON 1회→최종 검증)로 96문항 수집·채점. [집계](../reports/baseline-local-qwen35-4b-20261121.json) 응답 70·차단 26, 전체 동작 정답 52/96. 2026-11-22 시드만 바꾼 새 96문항에서 서버 규칙이 동작을 정하고 모델은 장소만 고르는 모드: 차단 0·동작 95/96, 같은 세트의 v1 55/96 ([33 문서 2.1절](33-huggingface-models-evaluation.md#21-고도화-실험-동작은-서버-규칙-모델은-장소-선택만-2026-11-22)). 동작 정답은 규칙끼리의 일치에 가깝고 장소 선택 지표는 v1과 비슷하다 | 걷기 최소화 경로 제외·문맥 확인 주제를 반영한 뒤 새 시드의 세 번째 세트로 확인. 작성 질의·사람 검수로 장소 선택 품질 확인 |
| multilingual-e5-large / Hugging Face Inference Providers | 장소 검색 질의 임베딩. 장소 벡터는 같은 가중치의 fp32 ONNX로 미리 계산 | 6종 비교와 SynthTRIPs 외부 점검에서 선택해 앱에 연결. 운영에서 결제 설정 전 `http_402`, 설정 뒤 원격 호출 성공(2026-11-22). 작성자 자유 질의 12개에서 규칙 검색 P@10 0.592→0.725 ([33 문서](33-huggingface-models-evaluation.md) 4절) | 원격·로컬 벡터 수치 일치, 실제 결과 기여 비율, 호출 비용 |
| Llama 3.2 3B Instruct / Ollama | 연결·JSON·도구 호출 비교 후보. `llama3.2:3b`는 확인 대기 태그 | 공식 모델카드 검토. 한국어는 공식 지원 언어에 없음. 실행 미수행 | 실제 모델 정보와 기능 확인, 한국어 품질은 별도 비교 |
| 임베딩 | 선택 모델 없음 | `embeddinggemma`는 공식 문서 예시로만 검토 | 한국어·제주 지명·별칭 검색 평가 후 모델·차원·인덱스 버전 결정 |
| 재정렬 모델 | 선택 모델 없음 | 도입·구현 미확정 | 규칙/BM25/hybrid 기준선 대비 순위 이득과 지연·비용 비교 |
| 학습 기반 페르소나 군집 | 선택 모델 없음 | 현재 8개 프로토타입은 수작업 설계이며 실제 응답으로 학습한 군집이 아님 | 동의된 응답을 확보한 뒤 Gower/PAM·임베딩/HDBSCAN 등 비교 |

코드 기본 문자열은 고정된 원본 소스의 값이고, 실제 반환 모델은 별도 실행 근거다. Gemini의 모델 ID는 응답 메타데이터에서 관측했으며 공식 공급자 전체 모델 목록을 검증한 결과는 아니다. OpenAI 기본값은 여전히 소스 확인 수준이다. 계정 권한 전체·과금 명세·환경변수 override 설정은 응답 성공과 별도로 확인한다.

초기 실행을 관측한 2026-11-18 UTC(한국 시간 11월 19일)의 근거는 레지스트리 출처 `gemini-live-baseline-observation`과 `langflow-vertex-smoke-observation`에 추가했다. `jeju-tour-e07a93e`는 독립 확인한 배포 SHA를 바탕으로 수집기가 지정한 도구 버전이다. API 응답이 보고한 필드가 아니며, 아래 원본 소스 핀 `b0602a1`과 별도로 기록한다. 2026-11-19 UTC에는 `langflow-live-baseline-observation`과 두 최종 집계 보고서를 확인했다. Langflow의 9.877초는 초기 단일 smoke의 관측 지연이며 기준선의 지연 분포는 최종 보고서를 따른다. 실행·집계 절차는 [25 실제 기준선 실행](25-live-baseline-execution.md), Vertex 연결 절차는 [26 Langflow 인증](26-langflow-vertex-auth.md)을 따른다. v1 집계와 모델 식별 근거를 역사 기록으로 유지하며 새 v2 기록은 아래에서 분리한다. 이번 기록 갱신으로 운영 모델·설정·배포를 변경하지 않았다.

기존 Gemini 앱과 Langflow 경로는 같은 주 모델 `gemini-3.8-flash`를 사용한다. 후보 카탈로그 범위와 날짜 문맥이 달라 이번 집계는 각 서비스 경로의 관측 결과로 읽어야 한다. 모델 우열을 입증한 비교가 아니며, Gemini의 혼합 대체 응답을 단일 3.8 성공에 합치지 않는다. 사람 검토와 조건을 맞춘 후속 비교는 별도 작업이다.

### v2 완료 결과와 남은 검증

[PR #9](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/9)의 v2 검증 경로는 `main`의 `4b41a03`으로 병합됐다. 후속 평가 작업은 [PR #10](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/10), 실행 계약·해석은 [28 v2 평가](28-agent-v2-evaluation.md), 수치 근거는 [최종 평가 보고서](../reports/baseline-langflow-v2-live-20261119.json)와 [런타임 감사 보고서](../reports/agent-v2-runtime-audit-20261119.json)다. JSON의 기존 `liveBaselineComparison`은 그대로 보존하고 `validatedV2Evaluation`을 별도로 추가했다.

| 항목 | v2 관측 결과 |
|---|---|
| 수집 | 96시도 = 응답 73 + 실패 23(`invalid_output` 14, `transport_error` 9), 미실행 0 |
| 전체 동작 정답 | 67/96; v1과 동일. 성공 응답만의 67/73과 구분 |
| 허용 밖 장소 ID | 7/295; v1 162/305. 후보 밖 5 + 최소 걷기 조건의 경로 2 |
| 확인 고지 | 요구 31건 중 17건; v1 요구 36건 중 35건 |
| 저장 메시지 | 최종 검증 87 = accepted 73 + blocked 14; 정제된 Agent 오류 9, 원문 Agent 0 |
| 사용량·비용 | 입력 1,275,298·출력 107,231 토큰 기록, 각각 8개 호출의 사용량 누락; 비용 `null` |

같은 981곳을 사용하지만 후보 계약이 완전히 같지는 않다. “쪽으로” 테마 선호는 `contextOnly`로 남기므로 평가의 테마 점수 0.5 이상 후보 규칙과 달라 ID 5개가 불허됐고, 최소 걷기 경로 2개는 런타임에서 강제 검증하지 못했다. 성공 응답의 동작 오답 6건은 날짜 충돌 5건과 걷기·제외 조건을 적용한 뒤 빈 후보 1건이다. 요청 포장·타입이 지정된 조건·최종 검증·시간/호출 상한까지 달라졌으므로 위 수치로 모델 우열이나 제품 품질 개선을 단정하지 않는다.

입력 변환기는 질문·문맥과 명시적 제외 장소의 이름 해석만 사용하며 평가 정답을 넣지 않는다. 고정 템플릿 문법 전용이므로 일반 자연어 이해 성공을 뜻하지 않는다. `theme_preference`, `companion`, `crowd_avoid`, `transport`, `walk`, `trip_dates` 여섯 조건은 원문에 보존하지만 강제 검증하지 못하고, 구조화 `noticeTopics`도 실내·영업시간·휠체어·비용 네 종류만 표현한다.

런타임 감사의 모델 span 255개는 모두 `gemini-3.8-flash`로 설정·기록돼 있다. 이는 공급자 응답에서 모델 ID를 독립 확인한 증거가 아니며 저장 상태 `ok` 248개를 최종 성공 73건과 혼동하지 않는다. MCP는 180회 모두 성공했다. Agent 90초·모델 최대 4회·공급자 60초·수집기 120초 상한이 적용됐고, 모델 호출 상한과 MCP 총호출 수는 별개여서 요청당 MCP 최대 7회가 관측됐다.

자동 테스트 240개(MCP 198 + Gateway 10 + Python 32)와 저장소 검사 14개를 통과했고 최종 CI는 PR의 최종 커밋에서 확인한다. 사람 검토 0/24건, 보안 평가 14건 미실행, 사용량 누락·비용 미측정은 남아 있다. 다음의 **검색 1회→일괄 제약 검사→최종 JSON** 경로는 제안 단계로 아직 구현·검증하지 않았다. 로컬 개발 범위에서 진행하며 운영 Linux·cloudflared·Gateway 구축이 필요해지면 멈추고 사용자에게 알린다.

원본 코드 연결점:

- [assistant.ts:24](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L24): 비서 모델 기본값.
- [placesearch.ts:17](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/placesearch.ts#L17): Vertex 장소 검색 모델 기본값. 인증은 Google 서비스 계정 경로이며 임의의 Gemini API 키 방식으로 바꾸지 않는다.
- [server.ts:1148](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L1148): `openaiWebJson()`의 Responses API·웹 검색.
- [server.ts:3114](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L3114): `explainWithOpenAI()`의 Chat Completions·JSON object 응답. 이 경로를 Responses로 이미 통합했다고 기록하지 않는다.

Claude Code에서 이 저장소를 개발하거나 HF MCP를 연결하는 것과 관광 앱이 Anthropic API를 호출하는 것은 별개다. 현재 사용 중인 개발 도구의 내부 모델을 이 레지스트리가 변경하지 않는다.

## 3. 모델·방법·호스트·도구를 분리한다

| 층 | 예 | 현재 의미 |
|---|---|---|
| 추론 엔진·공급자 | Vertex, OpenAI API, Anthropic API, Ollama | 모델 요청을 실행하는 계층 |
| 모델 | 정확한 API ID 또는 Ollama 태그+digest | 해당 엔진에서 선택한 체크포인트·버전 |
| 추천 방법 | 기존 규칙, 프로토타입 매칭, 논문 LAG, RAG 비교 실험 | 데이터 처리와 후보 선정 알고리즘. 엔진 이름과 동일하지 않음 |
| 호스트 | 기존 Vercel 앱, 제안 Worker, 사용자 Ollama PC | 실행 위치. 터널이나 모델 자체와 동일하지 않음 |
| 도구 연결 | 제주 MCP, Hugging Face MCP | 데이터·기능 호출 계약. 생성 모델이나 추천 알고리즘이 아님 |

현재 분석 저장소의 제주 MCP는 `places.search`, `places.get`, `constraints.check` 세 가지 읽기 도구를 구현했다. 이 구현에 LLM·LAG·장시간 작업 큐·운영 OAuth·외부 DNS 배포가 모두 포함된 것은 아니다. 상세 상태는 [13 구현·배포 문서](13-next-steps-and-mcp-implementation.md)를 따른다.

HF MCP는 연구용 서버다. [Codex 설정](../.codex/config.toml)은 공개 조회용 3개 도구, [Claude Code 설정](../.mcp.json)은 `files` preset을 가리킨다. 소스상 익명 공개 조회를 지원하므로 HF_TOKEN을 필수로 요구하지 않는다. 현재 클라우드는 HF 접근이 차단되어 운영 서버 `tools/list`·검색 성공을 확인하지 못했다. 환경 초안 저장, 실제 네트워크 적용, 새 세션에서 설정 로딩은 서로 다른 단계다.

HF의 `paper.md`는 본문이 없을 때 메타데이터·초록·AI 요약을 대신 반환할 수 있다. 2026-11-18 웹 조회로 `2504.09277`의 제목·초록을 확인한 결과 SynthTRIPs(관광 추천기 평가용 합성 질의 생성 틀)였고 LAG를 정의하지 않았다. 사용자 결정에 따라 LAG는 `2508.05509` Logic-Augmented Generation을 뜻한다([19 문서](19-agent-step2-data-foundation.md)). **두 논문 모두 본문 세부·필수 모델·재현 결과는 미확인**이다. LAG를 RAG로 바꾸거나 일반 도구 루프를 LAG 재현으로 표시하지 않는다. 평가 방법 `synthtrips-evaluation-generation`은 2026-11-18에 규칙 기반 정답과 세션 안에서 쓴 질의로 구현했다([23 문서](23-agent-step3-evaluation-set.md)). 같은 날 사용자 지시로 SynthTRIPs 저자 공식 코드 저장소(레지스트리 출처 `synthtrips-code`, 커밋 `9562f53`)를 읽고 생성 순서, 질의 200자 상한, 정답 이름 금지, 조건 반영 점검, recall·precision 채점을 대조해 맞췄다. 저자 코드는 실행하지 않았고 논문 본문과 Hugging Face 데이터셋은 확인하지 못했으므로 SynthTRIPs 재현이 아니다. 실제 Gemini 응답의 수집·정규화·채점을 완료했다. 세션 내 모델 주석은 [24 기준선 채점 문서](24-agent-baseline-scoring.md)의 `in_session_llm`로 기록하며 [집계 보고서](../reports/baseline-gemini-live-20261119.json)의 결과는 잠정 상태다. Langflow는 [구조화 응답 집계](../reports/baseline-langflow-live-20261119.json)를 완료했으며 출력 내용을 사람·모델 주석으로 덮어쓰지 않았다. 별도 외부 판정 모델 API를 실행했다는 뜻은 아니다. Gemini 모델 주석의 사람 24건 검토는 대기 중이며 사람 평가 완료로 표시하지 않는다.

현재 [페르소나 설정](../data/persona-prototypes.json)은 `persona-design-v1`의 8개 설계 후보다. [계산 코드](../scripts/persona_model.py)는 관측된 선호 축과 프로토타입의 거리를 계산한다. PCA 그림과 HTML 탐색기는 설계값을 표시하며 학습된 사용자 분포·군집 성능·심리 확률을 제공하지 않는다. 향후 군집 모델은 별도 버전으로 채택한다.

## 4. 두 로컬 후보의 선택 근거와 한계

**Qwen3-4B:** 공식 발표에 한국어를 포함한 119개 언어·방언, 4B 모델, Apache 2.0 및 도구 호출이 명시되어 있다. 한국어 평가를 먼저 진행할 근거이며 제주 관광 추천 우수성을 입증한 결과는 아니다. Qwen 공식 문서는 Ollama 이름이 원본 모델명과 다를 수 있다고 경고한다. `Qwen3-4B`, `Qwen3-4B-Instruct-2507`, `Qwen3-4B-Thinking-2507`을 같은 모델로 합치지 않는다. 정확한 태그가 정해지면 원본·thinking 모드·양자화·digest·라이선스를 함께 기록한다.

**Llama 3.2 3B:** 공식 크기는 3.21B 텍스트 모델이며 지원 언어는 영어·독일어·프랑스어·이탈리아어·포르투갈어·힌디어·스페인어·태국어다. 한국어를 기술적으로 전혀 처리할 수 없다는 뜻은 아니지만 공식 지원·제품 적합성을 주장하지 않는다. 추가 언어 적용 조건과 Community License/AUP를 검토한다. Ollama 런타임의 MIT 라이선스가 모델 사용 조건을 대체하지 않는다.

Llama 공식 일반 모델의 128k context와 Meta가 별도로 배포한 quantized 모델의 8k를 Ollama GGUF 태그에 그대로 대입하지 않는다. 실제 모델 메타데이터와 요청 `num_ctx`를 확인한다. HF 원본의 gated 접근 승인·토큰 요구를 Ollama registry 사용에 필요한 HF 계정 조건으로 확대하지 않는다.

현재 Windows/Mac OS·RAM·GPU/VRAM 또는 Apple 통합 메모리·여유 디스크 답변을 기다리고 있다. 특정 최소 RAM이나 초당 토큰 수는 확정하지 않는다. 가중치 크기 외에 KV cache, context, 연산 버퍼, 동시 요청, OS와 다른 프로세스가 메모리를 사용한다. 최초 검증 설정은 **context 4096·동시 요청 1개**를 제안하며 달성 성능이나 장비 최소치가 아니다.

## 5. 다음 세션의 검증 순서

현재 우선순위는 [28 문서](28-agent-v2-evaluation.md)의 실패 분석과 후보 계약·고지 한계를 반영해 검색 1회→일괄 제약 검사→최종 JSON 경로를 구현하고 검증하는 것이다. 아래 로컬 모델 후보 검증은 별도 트랙이며 v2 후속 구현이 완료됐다는 뜻이 아니다.

### A. 변경 없이 현재 상태부터 확인

1. 이 문서와 JSON, [09 Ollama/LAG](09-ollama-lag-feasibility.md), [10 MCP/Vercel](10-mcp-server-and-vercel-integration.md), [11 공급자·비용](11-multi-provider-tunnels-database-and-cost.md), [15 HF 설정](15-huggingface-mcp-setup.md)을 읽는다.
2. 기존 앱의 현재 배포 커밋과 모델 환경변수 이름·설정 여부를 확인한다. 값이 비밀인 키·서비스 계정·토큰은 출력하지 않는다. 기본 모델 문자열과 실제 선택을 구분한다.
3. 사용자 PC 사양, Ollama 설치 여부, 실행할 호스트를 확인한다. Vercel 함수의 localhost는 사용자 PC가 아니다.
4. HF 접근 정책이 실제 적용된 뒤 `tools/list`로 운영 도구·schema를 확인한다. 조회한 모델 카드·논문이 본문인지 요약인지 구분한다.

분석 저장소에서의 연구용 명령은 다음과 같다. 네트워크가 차단된 현재 상태에서는 성공을 기대하지 않으며, 경로 우회·TLS 검증 해제로 해결하지 않는다.

```sh
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs list
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs models "Korean instruction"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs paper 2504.09277
```

### B. 실행 가능성, 형식, 품질을 단계별로 검증

| 단계 | 검사 | 통과 기록 |
|---|---|---|
| 연결 | 설치된 Ollama 버전·`/api/version`, 모델 목록; API 공급자는 인증·계정 권한 | 서버와 통신했다는 사실. 추론 성공과 구분 |
| 모델 식별 | 실제 태그·digest·quantization·license·context·capabilities | 실행 명세. HF에 모델이 있다는 사실만으로 Ollama 호환 판정 금지 |
| 실제 추론 | 장비·모델 준비 후 짧은 한국어 생성, cold/warm 측정, `ollama ps` | 적재·응답·오류·CPU/GPU 배치·메모리·사용량 |
| 구조화 출력 | `/api/chat`에 JSON Schema를 전달하고 결과를 서버 schema로 재검증 | 형식 오류와 의미·사실 오류를 따로 기록 |
| 도구 왕복 | 읽기 도구 하나의 요청→인자 검사→실행→도구 결과 재입력 | 올바른 도구명·인자·허용 ID 사용·종료 조건 |
| 제품 적합성 | 같은 설문·장소 스냅샷으로 규칙/Gemini/OpenAI/로컬 후보 비교 | 한국어 해석, 지명 보존, ID·근거·제약, 지연·비용 결과 |

위 Gemini·Langflow 실측과 초기 smoke는 실제 API 실행 근거이며 모델 다운로드나 다른 후보의 실행 근거로 확대하지 않는다. 추가 실행 시에는 당시 사용자 지시와 계정·예산 범위를 따른다. 공식 API의 JSON object 모드, JSON Schema, 도구 호출 형식이 서로 같다고 가정하지 않는다. Ollama Cloud는 조사한 공식 문서에서 structured outputs 미지원으로 구분하므로 로컬 결과를 Cloud에도 적용하지 않는다.

평가 사례에는 긍정 선호뿐 아니라 “거의 안 걸어요”, 카페 필수/선호 구분, 알 수 없는 접근성, 날짜 충돌, 허용되지 않은 장소 ID, 빈 검색 결과를 포함한다. 모델이 그럴듯한 장소를 만들어 채우거나 필수 제약을 무시하면 유창한 설명과 무관하게 실패로 기록한다. 상세 실험 기준은 [03 평가·로드맵](03-evaluation-and-roadmap.md)을 따른다.

## 6. 라우터·fallback·응답 호환

공통 `ModelRouter → 공급자별 어댑터`는 제안 구조이며 현재 운영에 구현된 상태가 아니다. 초기에는 한 요청·한 시도에 한 공급자를 선택한다. OpenAI와 Claude의 병렬 비교는 별도 평가 예산에서만 수행하고 각 호출·도구·재시도 비용을 합산한다.

공급자 변경은 원인·권한·비용·개인정보 전송 범위를 확인한 정책 안에서만 수행한다. 로컬 전용 입력을 장애 시 외부 API로 조용히 보내지 않는다. 대체 엔진을 허용하지 않았다면 동일한 필수 제약을 유지하는 규칙 경로 또는 명시적 실패를 반환한다. 해당 장소의 접근성·가격·시간이 미확인이면 unknown을 유지한다.

기존 `AssistantReply.engine`은 `gemini | keyword | none`이다. OpenAI·Claude·Ollama를 `gemini`로 위장하거나 새 문자열을 구형 웹·Flutter에 바로 보내지 않는다. 새 버전 계약과 호환 어댑터를 구현하면서 `provider`, `engine`, `modelId`, `method`, `attemptId`, `dataVersion`, `degraded`, `fallbackReason`을 분리한다. 실제 코스 저장은 사용자의 기존 적용 동작과 소유권·버전 검증을 유지한다.

실험 기록에는 모델 ID/digest, 엔진·버전, 프롬프트/schema, 방법·프로필·데이터 버전, 하드웨어, context, 동시성, 지연, 토큰/도구 사용량을 남긴다. 움직이는 별칭만 기록하면 같은 실험을 재현할 수 없다. 현재 없는 측정값을 0이나 성공으로 채우지 않는다.

## 7. 계정·비용·비밀값·개인정보 경계

- **Vertex:** 기존 Gemini와 로컬 Langflow의 실제 추론 성공을 확인했다. 사용한 요청 범위 밖의 IAM 권한·모델 접근·과금 상태까지 검증한 것은 아니며, 기존 Google 서비스 계정·프로젝트 범위를 별도로 관리한다.
- **OpenAI/Anthropic:** 운영자가 API 프로젝트·자격·과금을 관리한다. 여행자에게 공급자 계정을 만들게 하지 않는다. ChatGPT/Claude 채팅 구독과 API 과금은 별도이며 최신 가격·무료 크레딧은 이번에 검증하지 않았다.
- **Ollama:** 로컬 실행은 외부 API 구독과 별개다. 장비·전기·저장소·GPU 임대 비용은 남는다. 로컬 전용 운영은 실제 모델의 원격 호스트 여부와 Cloud 비활성화를 함께 확인한다.
- **키 범위:** 기존 Vercel 호출용 키와 미래 Worker 자격은 이관 전까지 역할을 분리한다. 키·토큰·서비스 계정 원문을 이 JSON, Git, 브라우저, 프롬프트, MCP 출력에 넣지 않는다. HF 공개 연구 조회에는 현재 토큰이 필수 조건이 아니다.
- **사용자 자료:** 동의·보존 정책 안에서 필요한 최소 설문 필드와 장소 근거만 모델에 전달한다. HF 모델·논문 검색에는 개인 대화·연락처·숙소 위치·접근 토큰을 넣지 않는다. 실측 평가에 실제 사용자를 무단 복제하지 않는다.
- **도구 출력:** 출처 본문·MCP 결과·모델 출력은 데이터로 처리한다. 그 내용으로 모델 ID·인증·대상 URL·허용 도구·저장 권한을 변경하지 않는다.
- **공개 경계:** Ollama raw 11434를 인터넷에 노출하지 않는다. Cloudflare/게이트웨이·MCP 인증은 모델 선택과 별도이며 터널이 많아져도 공급자 한도나 GPU 성능이 늘어나지 않는다.

## 8. 레지스트리 갱신 규칙과 완료 기준

새 사실을 얻으면 MD와 JSON을 같은 변경에서 갱신한다. 실제 모델 ID·digest·하드웨어를 얻기 전 `null`을 추정값으로 채우지 않는다. 자료 검토→연결→추론→형식/도구→제품 평가 각각의 날짜·조건·결과를 별도로 기록한다. 모델 교체 후에는 동일한 고정 평가 사례를 실행하고 이전 기준선·실패 사례를 보존한다.

다음 상태가 모두 확보되기 전 “운영 준비 완료”, “LAG 재현 완료”, “한국어 최적”으로 올리지 않는다: 정확한 모델 명세, 검증된 실행 환경, 형식·도구 왕복, 근거·필수 제약 평가, 허용된 전송·예산, 실패·fallback·롤백 계약. LAG에는 추가로 원문 검토와 요구 알고리즘의 재현 실험이 필요하다.

## 9. 고정된 공식 근거

- [Ollama 도구 호출](https://github.com/ollama/ollama/blob/b8db8d5b5c8daedbbef7bb9ace2221a38bc384c2/docs/capabilities/tool-calling.mdx), [구조화 출력](https://github.com/ollama/ollama/blob/b8db8d5b5c8daedbbef7bb9ace2221a38bc384c2/docs/capabilities/structured-outputs.mdx), [모델 가져오기](https://github.com/ollama/ollama/blob/b8db8d5b5c8daedbbef7bb9ace2221a38bc384c2/docs/import.mdx).
- [Meta Llama 3.2 모델카드](https://github.com/meta-llama/llama-models/blob/0e0b8c519242d5833d8c11bffc1232b77ad7f301/models/llama3_2/MODEL_CARD.md), [Community License](https://github.com/meta-llama/llama-models/blob/0e0b8c519242d5833d8c11bffc1232b77ad7f301/models/llama3_2/LICENSE).
- [Qwen3 발표·지원 언어·라이선스](https://github.com/QwenLM/qwenlm.github.io/blob/1f89f8c81adb85e3808f9cc15180ab1fdacbb900/content/blog/qwen3/index.md), [공식 Ollama 실행·태그 주의](https://github.com/QwenLM/Qwen3/blob/7a2f61ffc7a20d47efcd2bf97f6f2bf52729042e/README.md#L239).
- [HF MCP 공식 소스](https://github.com/huggingface/hf-mcp-server/tree/16ebc9579c25266032ac2234414b9dd98fd96cf4), [현재 입력 schema](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/mcp/src/hf-fs-contract.ts), [논문 fallback](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/mcp/src/hf-fs-papers.ts#L1190).
- [OpenAI 공식 인증 예시](https://github.com/openai/openai-python/blob/f56a67f1f0c844135261ece304a90d2ec2571d4b/README.md), [Anthropic 공식 인증 예시](https://github.com/anthropics/anthropic-sdk-python/blob/10c82e8e8a968f624797df48d6115c4cc6b32551/README.md). 인증 예시는 특정 모델의 현재 가용성·요금 검증이 아니다.

로컬 scratch 체크아웃은 조사 편의를 위한 임시 자료다. 다음 세션에 반드시 존재한다고 가정하지 않고 위 고정 URL·커밋과 저장소 문서로 근거를 이어간다.
