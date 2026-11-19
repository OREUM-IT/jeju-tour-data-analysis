# AI Agent 모델 전체 현황과 다음 검증 절차

기준일: 2026-11-19 한국 시간. 레지스트리·보고서 확인일도 2026-11-19 UTC이며 개별 실행의 원래 UTC 시각은 보고서에 보존한다. **기존 Gemini `/api/assistant`와 로컬 Langflow 1.13.3의 고정 평가 계획 전체를 실제 수집·정규화·채점했다. Gemini 자유 문장 채점은 세션 내 모델 주석을 사용한 잠정 결과이며 사람 24건 검토가 남았다. Langflow는 구조화 응답을 자동 채점했다. 두 경로의 주 모델은 `gemini-3.8-flash`이고 기존 비서의 `gemini-3.5-flash-lite` 혼합 대체 기록도 보존했다. OpenAI 실제 호출은 미검증, Claude는 미연동, Ollama 모델은 미실행이다.**

이 문서와 [머신 판독용 목록](../data/ai-agent-models.json)은 다른 Claude Code 세션이 현재 근거와 미완료 항목을 이어받기 위한 기록이다. 모델 설치·교체·API 호출·운영 배포를 자동 실행하는 설정 파일이 아니다. `Update all AI AGENT MODEL` 요청에 따라 사용 중인 경로·후보·미선정 역할을 함께 갱신했으며, 모델을 모두 다운로드하거나 운영 기본값을 일괄 변경하지 않았다.

## 1. 기록을 읽는 기준

| 근거 수준 | 확인한 것 | 이 근거만으로 주장할 수 없는 것 |
|---|---|---|
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
| Langflow / Vertex AI Agent | Langflow 1.13.3, 실제 반환 `gemini-3.8-flash` | Vertex·제주 MCP를 사용한 실제 응답 수집·정규화·구조화 자동 채점 완료. [최종 집계](../reports/baseline-langflow-live-20261119.json) | 실패·허용 밖 ID 원인 검토, 비교 조건 정렬, 운영 품질 검증 |
| OpenAI | `OPENAI_MODEL`, 코드 기본 `gpt-5.4-mini` | Responses+웹 검색, Chat Completions 일정 설명 코드 존재. 키 설정·재배포는 사용자 보고; 실제 호출 미검증 | 두 API 경로를 각각 확인하고 반환 모델·오류·사용량 기록 |
| Claude / Anthropic API | 정확한 모델 ID 미선정 | 공급자 어댑터 설계만 있음. 관광 앱 미연동 | API 계정의 사용 가능 모델·데이터 정책·비용 확인 후 어댑터 구현 |
| Qwen3-4B / Ollama | 한국어 제품 평가 우선 후보. `qwen3:4b`는 확인 대기 태그 | 공식 언어·라이선스·실행 문서 검토. 모델 적재·한국어 평가 미수행 | 태그가 가리키는 정확한 변형·양자화·digest 확인 후 공통 평가 |
| Llama 3.2 3B Instruct / Ollama | 연결·JSON·도구 호출 비교 후보. `llama3.2:3b`는 확인 대기 태그 | 공식 모델카드 검토. 한국어는 공식 지원 언어에 없음. 실행 미수행 | 실제 모델 정보와 기능 확인, 한국어 품질은 별도 비교 |
| 임베딩 | 선택 모델 없음 | `embeddinggemma`는 공식 문서 예시로만 검토 | 한국어·제주 지명·별칭 검색 평가 후 모델·차원·인덱스 버전 결정 |
| 재정렬 모델 | 선택 모델 없음 | 도입·구현 미확정 | 규칙/BM25/hybrid 기준선 대비 순위 이득과 지연·비용 비교 |
| 학습 기반 페르소나 군집 | 선택 모델 없음 | 현재 8개 프로토타입은 수작업 설계이며 실제 응답으로 학습한 군집이 아님 | 동의된 응답을 확보한 뒤 Gower/PAM·임베딩/HDBSCAN 등 비교 |

코드 기본 문자열은 고정된 원본 소스의 값이고, 실제 반환 모델은 별도 실행 근거다. Gemini의 모델 ID는 응답 메타데이터에서 관측했으며 공식 공급자 전체 모델 목록을 검증한 결과는 아니다. OpenAI 기본값은 여전히 소스 확인 수준이다. 계정 권한 전체·과금 명세·환경변수 override 설정은 응답 성공과 별도로 확인한다.

초기 실행을 관측한 2026-11-18 UTC(한국 시간 11월 19일)의 근거는 레지스트리 출처 `gemini-live-baseline-observation`과 `langflow-vertex-smoke-observation`에 추가했다. `jeju-tour-e07a93e`는 독립 확인한 배포 SHA를 바탕으로 수집기가 지정한 도구 버전이다. API 응답이 보고한 필드가 아니며, 아래 원본 소스 핀 `b0602a1`과 별도로 기록한다. 2026-11-19 UTC에는 `langflow-live-baseline-observation`과 두 최종 집계 보고서를 확인했다. Langflow의 9.877초는 초기 단일 smoke의 관측 지연이며 기준선의 지연 분포는 최종 보고서를 따른다. 실행·집계 절차는 [25 실제 기준선 실행](25-live-baseline-execution.md), Vertex 연결 절차는 [26 Langflow 인증](26-langflow-vertex-auth.md)을 따른다. 집계 수치는 연결한 보고서를 기준으로 확인하며 이 문서에 중복 기록하지 않는다. 이번 기록 갱신으로 운영 모델·설정·배포를 변경하지 않았다.

기존 Gemini 앱과 Langflow 경로는 같은 주 모델 `gemini-3.8-flash`를 사용한다. 후보 카탈로그 범위와 날짜 문맥이 달라 이번 집계는 각 서비스 경로의 관측 결과로 읽어야 한다. 모델 우열을 입증한 비교가 아니며, Gemini의 혼합 대체 응답을 단일 3.8 성공에 합치지 않는다. 사람 검토와 조건을 맞춘 후속 비교는 별도 작업이다.

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
