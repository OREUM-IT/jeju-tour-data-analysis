# AI Agent 1·2단계: 구축 가능성 진단과 데이터 기반

기준일: 2026-11-18 (한국 시간). 이 문서는 Claude 세션의 실측(관측), 사용자 결정, 제안을 나눠 기록한다. 실측은 이 문서에 적은 커밋·명령으로 다시 확인할 수 있다. 운영 배포, 모델 추론, 실제 관광객 평가는 하지 않았다.

## 1. 1단계 진단: 만들 수 있다, 다만 출발점은 '설계 + 읽기 전용 도구'

**결론(관측 기반 판단):** 제주 개인화 관광 Agent는 이 저장소와 원본 앱을 바탕으로 만들 수 있다. 진단 시점(main `4cf3547`)의 저장소에는 상세 설계, 데이터 목록, 읽기 전용 MCP 도구 3개가 있었다. 반면 LLM이 도구를 골라 부르는 Agent 루프, 추천에 쓸 수 있는 장소 속성, 평가셋은 없었다.

| 확인 항목 | 관측 결과 | 비고 |
|---|---|---|
| 기존 자동 검사 | MCP·HF 클라이언트 32개, Gateway 10개 통과. `check_repository.py` 전 항목 PASS | 로컬 Node 22.23.5. CI 기준 24.22.1과 다르다 |
| 실제 POI로 MCP 왕복 | 7,561건 가져오기, SDK 클라이언트의 초기화·도구 3개 호출 성공 | 원본 앱 `744f4c1`의 POI 파일. 고정 커밋 `b0602a1`과 바이트 동일 |
| `constraints.check` 실효성 | 7,561곳 모두 제약 값·근거가 없어 항상 `unknown` | 가져오기 단계가 `in`·`h`·`hv` 등을 의도적으로 제외한다 |
| `places.search` 자연어 | "바다 보이는 조용한 카페" 0건 | 글자 일치 검색이다 |
| 중복 | 성산일출봉 2건(`cur-16`, `osm-n330006952`) | v1 카탈로그는 앱의 중복 정리 이전 원시 POI다 |
| 분류 체계 | 페르소나 9테마, 앱 10분류, 원시 POI 8분류 사이 대응표 없음 | 2단계에서 매핑표 추가 |
| 원본 앱 자산 | Gemini 2단계 비서(요청 해석→서버 후보→후보 ID 안에서만 답변, 실패 시 규칙 검색), 날씨·혼잡 예측·카카오 길찾기 | `assistant.ts`, `kma.ts`, `crowdapi.ts`, `kakao-routing.ts` 소스 확인. 배포 동작은 미확인 |

원본 앱은 이미 Agent의 절반에 해당하는 고정 파이프라인을 가지고 있다. 따라서 새 Agent는 앱의 장소 ID와 계약을 그대로 쓰고, 도구 호출·제약 검증·평가를 덧붙이는 방향이 재작업이 가장 적다.

## 2. 논문 전제 정정과 사용자 결정

| 구분 | 내용 |
|---|---|
| 관측 | arXiv `2504.09277`의 제목은 *SynthTRIPs: A Knowledge-Grounded Framework for Benchmark Query Generation for Personalized Tourism Recommenders*(SIGIR 2025)다. 페르소나와 예산·계절·인기도·지속가능성 같은 조건을 묶고, 지식베이스에서 맞는 사실을 찾은 뒤 LLM으로 관광 질의를 합성한다. 추천기 **평가용 벤치마크 생성 틀**이며 LAG라는 방법은 나오지 않는다. |
| 관측 | 제목이 LAG인 논문은 arXiv `2508.05509` *LAG: Logic-Augmented Generation from a Cartesian Perspective*다. 복합 질문을 논리 의존 순서의 하위 질문으로 나눠 앞선 답으로 다음 검색을 이끌고, 답할 수 없는 하위 질문에서 추론을 멈춘다. |
| 사용자 결정 (2026-11-18) | 둘 다 쓴다. SynthTRIPs는 3단계 평가셋 생성 틀로, LAG(`2508.05509`)는 5단계 Agent 추론의 비교 실험으로 쓴다. |
| 한계 | 두 논문 모두 초록·HTML 요약 수준으로 확인했다. 본문의 세부 절차·수치·코드는 검토하지 않았다. 재현 전에는 결과에 `method=lag`를 붙이지 않는다. |

[모델 레지스트리](../data/ai-agent-models.json)의 출처를 `synthtrips-paper`와 `lag-logic-augmented-generation`으로 나눴다. `paper-lag` 방법은 후자를 가리키고, 평가 방법 `synthtrips-evaluation-generation`을 추가했다.

## 3. 단계적 절차

| 단계 | 할 일 | 완료 기준 |
|---|---|---|
| 1. 진단 | 저장소·원본 앱·테스트·논문 확인 | 이 문서 1·2절 (완료) |
| 2. 데이터 기반 | 앱과 같은 장소 목록을 근거 등급과 함께 카탈로그로, 테마↔분류 매핑 | 이 문서 4~8절 (완료) |
| 3. 평가셋 | SynthTRIPs 방식으로 8개 페르소나 × 조건(날짜·이동수단·동행·걷기·실내·혼잡·예산 미정)의 제주 질의와 정답 후보 집합 생성 | 질의마다 근거가 된 카탈로그 사실·난이도·기대 동작(추천/확인 요청/불가 안내)이 있다 |
| 4. 도구 | 페르소나 매칭, 후보 랭킹, 근거 등급 조건 검사, 날씨·혼잡, 일정 검증을 MCP 도구로 | 도구마다 스키마·단위 테스트와 `unknown`을 통과로 바꾸지 않는 검사 |
| 5. Agent 루프 | 프로필 확인→후보→일정→검증→설명. 후보 ID 밖 장소 금지 유지. LAG 방식을 비교 실험으로. 시각적 편집은 [Langflow](20-langflow-agent-builder.md)(사용자 결정) | 3단계 평가셋에서 허용되지 않은 ID 0건, 필수 조건 위반 0건 |
| 6. 비교·연결 | 기존 Gemini 비서와 같은 평가셋으로 채점 후 앱 Preview에 기능 플래그로 연결 | 비교 결과와 되돌리기 절차. 운영 배포는 별도 승인 |

## 4. 2단계 산출물

| 파일 | 역할 |
|---|---|
| [`data/theme-category-map.json`](../data/theme-category-map.json) | 페르소나 9테마와 앱 10분류·종류를 잇는 설계 가중치 v1 |
| [`services/jeju-mcp/src/agent-catalog.mjs`](../services/jeju-mcp/src/agent-catalog.mjs) | 카탈로그 v2 생성·검증, 테마 가중치, 중복 후보 검토, 집계 보고서 |
| [`services/jeju-mcp/scripts/dump-app-places.mjs`](../services/jeju-mcp/scripts/dump-app-places.mjs) | 별도 작업 폴더에 빌드한 앱 사본에서 `/api/course/places`를 loopback으로 받기 |
| [`services/jeju-mcp/scripts/export-agent-catalog.mjs`](../services/jeju-mcp/scripts/export-agent-catalog.mjs) | 앱 장소 목록 + 같은 커밋의 원시 POI → 카탈로그 v2. 원시 파일 바이트를 커밋과 대조 |
| [`services/jeju-mcp/tests/agent-catalog.test.mjs`](../services/jeju-mcp/tests/agent-catalog.test.mjs) | 합성 값으로 근거 등급·검증 거부·집계 전용·CLI·loopback 동작 9개 검사 |
| [`reports/agent-catalog-coverage.json`](../reports/agent-catalog-coverage.json) | 장소 이름·ID 없이 개수만 담은 커버리지 보고서 |

카탈로그 본체와 앱 장소 덤프에는 제3자 장소 자료가 들어 있으므로 `services/jeju-mcp/.local/`에만 쓰고 Git에 넣지 않는다. `check_repository.py`는 테마 매핑 규칙과 보고서의 집계 전용 여부(장소 ID 패턴 없음)를 함께 검사한다.

## 5. 재현 절차

참고 저장소 checkout에는 설치·빌드하지 않는다. 고정 커밋을 별도 작업 폴더로 풀어 빌드한다. 아래는 이번에 실행한 순서이며 환경변수·비밀값 없이 장소 목록이 나왔다.

```sh
# 1) 참고 앱 고정 커밋을 별도 작업 폴더로 풀어 빌드
git -C <jeju-tour checkout> fetch --depth 1 origin b0602a175c1d08959b7187da2b29ac450b52e0ba
mkdir -p <scratch>/app-b0602a1
git -C <jeju-tour checkout> archive b0602a175c1d08959b7187da2b29ac450b52e0ba prototype | tar -x -C <scratch>/app-b0602a1
cd <scratch>/app-b0602a1/prototype
npm ci --ignore-scripts --no-audit --no-fund
npx esbuild src/server.ts --bundle --platform=node --format=cjs --packages=external --target=node22 --outfile=dist/server.js
node scripts/copy-migrations.cjs && node scripts/copy-public-service.cjs

# 2) 앱이 스스로 만든 장소 목록을 loopback으로 받기 (.local 전용, 덮어쓰지 않음)
cd <analysis repo>/services/jeju-mcp
node scripts/dump-app-places.mjs --app-dir <scratch>/app-b0602a1/prototype --output .local/app-places-b0602a1.json

# 3) 근거 등급 카탈로그와 집계 보고서
node scripts/export-agent-catalog.mjs --places .local/app-places-b0602a1.json \
  --raw <jeju-tour checkout>/prototype/data/jeju_tour_data.json \
  --commit b0602a175c1d08959b7187da2b29ac450b52e0ba \
  --output .local/agent-catalog-b0602a1.json --report ../../reports/agent-catalog-coverage.json
```

관측: 앱 장소 목록은 두 번 실행해도 `placesContentSha256`이 같았다(`e3c47e84…`). 원본 앱 `744f4c1`은 `b0602a1` 이후 커밋이지만 장소 목록을 만드는 소스·데이터 파일(`course.ts`, `course-categories.ts`, `leisure-places.ts`, `jeju_tour_data.json`, `barrier_free.json`, `jeju_drama.json`, `olle_official.json`)에는 차이가 없었다. 그사이 변경은 티켓·회원 DB 스키마·허브 카탈로그 쪽 8개 소스 파일이다. 앱 장소 목록 JSON의 커밋은 실행자가 지정한 값이다. 대신 원시 파일 바이트를 그 커밋과 대조하고, 원시 POI에서 온 장소 이름이 한 건이라도 다르면 내보내기를 멈춘다(이번 0건).

## 6. 근거 등급과 Agent 사용 규칙

| 등급 | 뜻 | Agent가 할 수 있는 것 | 하지 않는 것 |
|---|---|---|---|
| `verified` | 필드별 확인일·유효기간이 있는 근거 | 필수 조건 pass/fail 판정 | 이번 내보내기에는 0건 |
| `source_reported` | 이름 있는 외부 자료의 값(카카오 영업시간, 관광빅데이터 방문 지표, 무장애여행정보, 공식 경로 자료) | 순위 반영, "방문 전 확인 필요"와 함께 안내 | 필수 조건 pass |
| `app_curated` | 원본 앱 데이터·코드에 사람이 넣은 값(`in`·`nat`·`dur` 필드, 지정 목록, 안내 문구) | `source_reported`와 같게 다룸 | 필수 조건 pass |
| `inferred` | 분류·종류·이름 규칙 추정(실내=음식·카페·전시 분류, 기본 체류 시간) | 선호 신호 | 사실처럼 말하기, 필수 조건 판단 |
| `computed` | 결정적 계산(권역, 인기 순위, 테마 가중치) | 정렬·필터 | 원래 필드보다 강한 근거로 취급 |

휠체어 필수·비 오는 날 실내 필수·영업시간·예산 같은 필수 조건은 `verified`가 없으면 `unknown`이다. Agent는 이때 조건을 조용히 완화하지 않고 확인이 필요한 후보와 그 이유를 보여 준다.

## 7. 실측 커버리지 (`b0602a1`, 981곳)

앱 장소 981곳 중 원시 POI에서 온 곳은 863곳, 앱이 다른 자료(무장애여행정보·올레·탐방로·드라마·자전거길·레저)로 만든 곳은 118곳이다.

| 속성 | 값 있음 | 근거 분포 |
|---|---:|---|
| 영업시간 | 514 | 카카오 출처 454 · 앱 작성 12 · 경로 기본값 추정 48 |
| 실내 여부 | 981 | 앱 데이터 표기 132 · 분류 규칙 추정 849 |
| 자연 여부 | 981 | 앱 데이터 표기 95 · 추정 886 |
| 체류 시간 | 858 | 큐레이션 47 · 기본값 추정 811 (숙소 제외) |
| 방문 지표 | 637 | 제주관광빅데이터 637 |
| 무장애여행정보 목록 | 121 | 제주특별자치도 무장애여행정보 121 |
| 지정 목록(100선·유네스코·열린관광지) | 48 | 앱 이름 목록 |
| 경로 정보 | 48 | 올레·탐방로·하영올레·자전거길 |
| 비용 | 0 | 없음 |
| `verified` | 0 | 없음 |

필수 조건 기준으로 보면, 휠체어 관련 표기가 있는 곳은 125곳이고 856곳은 정보가 없다. 실내를 출처로 뒷받침하는 곳은 132곳뿐이며, 나머지 849곳은 분류 추정이다. 영업시간 출처가 있는 곳은 466곳이다. 비용은 한 곳도 판정할 수 없다. 즉 예산 조건은 4단계 이후에도 "확인 필요"로 안내해야 하며, 비용 자료 확보가 데이터 보강 1순위다.

테마별로 가중치 0.5 이상인 장소 수는 해안 109, 숲·자연 133, 로컬 음식 201, 카페 142, 역사·문화 37, 예술 101, 야외 활동 37, 체험·배움 68, 동네 산책 7이다. 동네 산책·역사·문화·야외 활동은 후보가 적다. 단일 장소 분류로는 동네 산책을 표현하기 어려워, 권역 단위 동선 조합이 필요하다(매핑 `knownGaps`).

## 8. 중복 후보 검토

같은 이름 3km 이내 또는 이름 포함 관계 300m 이내 규칙으로 8쌍을 표시했다. 사람이 검토한 결과, 같은 시설이 두 분류로 들어간 것으로 보이는 쌍은 `유리의 성 (Glass Castle)`(art)과 `유리의성`(activity) 1쌍이다. 나머지는 올레·자전거길 시작점과 명소, 시장 안 카페, 명소 옆 시설처럼 서로 다른 장소다. 내보내기는 앱 ID를 바꾸거나 지우지 않는다. 정리는 원본 앱 소유자의 결정으로 남긴다.

## 9. 한계

- 원본 앱의 분류·중복 정리·실내 추정 규칙을 그대로 따른다. 그 규칙의 오류는 카탈로그에 그대로 남고, 근거 등급으로만 드러난다.
- 카카오 영업정보는 원본 앱의 수집 자료다. 이용 조건 검토가 끝나기 전에는 카탈로그를 공개 저장소·외부 서비스에 올리지 않는다.
- 테마 매핑 v1은 수작업 설계 가설이다. 실제 방문·만족 자료로 보정하지 않았다.
- 로컬 검증은 Node 22.23.5에서 했다. CI는 Node 24.22.1에서 같은 테스트를 실행한다.

## 10. 다음: 3단계 평가셋 제안

SynthTRIPs의 순서를 제주에 맞춘다. 페르소나 8개와 조건 묶음을 만들고, 조건에 맞는 카탈로그 사실을 먼저 찾는다. 맞는 사실이 없는 조합은 버리거나 '불가 안내'가 정답인 사례로 남긴다. 그다음 LLM이 그 사실에 근거해 자연어 질의를 쓴다. 정답 후보 집합은 LLM이 아니라 근거 등급 규칙으로 계산한다. 난이도는 단순·복합·충돌로 나누고, [모델 레지스트리 문서](17-ai-agent-model-registry.md)의 어려운 사례(거의 안 걸음, 알 수 없는 접근성, 날짜 충돌, 허용되지 않은 ID, 빈 검색 결과)를 반드시 넣는다. 합성 질의는 실제 관광객 응답을 대신하지 않으며, 사용자 평가와 분리해 기록한다.
