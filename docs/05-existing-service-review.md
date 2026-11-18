# 기존 제주 관광 서비스 소스 검토와 확장 방향

## 1. 검토 대상과 확인 수준

사용자가 지정한 서비스는 `https://jeju-tour-six.vercel.app/`이다. GitHub 조직의 공개 목록에서 관련 Flutter 클라이언트를 찾았고, 해당 README가 원본을 `OREUM-IT/jeju-tour`로 명시했다. 이후 환경에 이미 제공된 Git 인증으로 원본 저장소 읽기와 복제에 성공했다.

| 대상 | 검토한 고정 버전 | 이번 작업의 확인 범위 |
| --- | --- | --- |
| 원본 웹·서버 | [`OREUM-IT/jeju-tour`, `b0602a175c1d08959b7187da2b29ac450b52e0ba`](https://github.com/OREUM-IT/jeju-tour/tree/b0602a175c1d08959b7187da2b29ac450b52e0ba) | 파일·코드·커밋된 데이터의 정적 검토 |
| 관련 Flutter 클라이언트 | [`OREUM-IT/jeju-tour-flutter`, `81e5f2acc1b5fd947b183fe1d74b6707717518e7`](https://github.com/OREUM-IT/jeju-tour-flutter/tree/81e5f2acc1b5fd947b183fe1d74b6707717518e7) | 원본 서비스 연결 관계와 구현 범위 문서 확인 |
| 운영 사이트 | 사용자가 지정한 Vercel 주소 | HTTPS 접속이 프록시의 `Tunnel connection failed: 403 Forbidden`으로 차단되어 현재 화면·API·배포 커밋은 확인하지 못함 |

검토 날짜는 2026-11-18 한국 시간 기준이다. 소스 체크아웃은 `/workspace/scratch/reference-repos/` 아래에 참고용으로 보관했으며 기존 서비스의 코드·설정·운영 데이터는 수정하지 않았다. 이번 검토에서는 기존 서비스 설치·빌드·테스트·유료 모델 호출·운영 API 쓰기를 수행하지 않았다. 저장소에 기록된 과거 검증 결과를 이번 실행 결과로 간주하지 않는다.

Flutter README의 “원본 소스에 접근하지 못했다”는 설명은 해당 클라이언트를 작성하던 당시의 기록이다. 이번 작업에서는 원본 Git 접근이 확인됐으므로, 설문과 추천 로직의 근거는 Flutter 재구현이 아닌 위 원본 커밋을 우선한다.

## 2. 실제 설문 문항과 응답 값

근거: [`prototype/client/features/home/ThemeQuiz.tsx:20`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/features/home/ThemeQuiz.tsx#L20).

| 순서·키 | 실제 문항 | 실제 선택지와 내부 값 |
| --- | --- | --- |
| 1 · `style` | 이번 제주 여행, 어떤 분위기가 끌리나요? | 레저·스포츠 `leisure`; 바다·해안 경관 `sea`; 오름·숲 힐링 `heal`; 문화·역사·전통 `herit`; 예술·전시 `art`; 테마파크·체험 `activity` |
| 2 · `must` | 빠질 수 없는 건? | 맛집·시장 `food`; 카페·디저트 `cafe`; 상관없어요 `none` |
| 3 · `walk` | 걷기나 등산은 얼마나? | 올레길 한 코스쯤 `olle`; 한라산에 도전 `hike`; 거의 안 걸어요 `none` |
| 4 · `local` | 제주에서는 어떻게 다니나요? | 렌트카 `driving`; 버스·택시 `transit` |

선택하면 다음 문항으로 이동하는 4단계 모달이며 이전·닫기 기능이 있다. 결과에서 추천 테마, 장소 목록, 코스빌더에 넣기, 테마 장소 보기, 다시 고르기를 제공한다. 한라산 선택에는 등반 플래너 이동 안내가 추가된다. 홈과 테마 화면에서 같은 설문을 연다.

이 설문에는 현재 예산, 동행 인원, 접근성의 필수 조건, 여행 날짜, 권역, 총 활동 가능 시간, 알레르기 등을 직접 받는 문항이 없다. 다른 화면에 존재하는 조건 입력과 “이 4문항 설문에서 받는 정보”를 구분해야 한다.

## 3. 현재 설문 추천은 규칙 기반

근거: [`ThemeQuiz.tsx:35`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/features/home/ThemeQuiz.tsx#L35), [`ThemeQuiz.tsx:58`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/features/home/ThemeQuiz.tsx#L58), [`nearest-order.ts`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/features/home/nearest-order.ts).

1. 첫 응답 `style`을 추천 테마 분류로 그대로 사용한다. 별도의 잠재 페르소나 추론이나 학습 모델 분류는 이 함수에 없다.
2. 해당 분류이며 `auto === true`인 장소를 후보로 고른다. 장소 점수는 `(popRank ?? 200) - 20 × badges.length`이고 **낮은 점수가 우선**이다.
3. `must`가 맛집 또는 카페이면 주 테마 3곳과 해당 분류 1곳을 고른다. 상관없어요이면 주 테마 4곳을 고른다. 후보 수가 부족하면 요청 수보다 적을 수 있다.
4. `walk === 'olle'`이면 현재 선택 장소 좌표의 평균점에서 직선거리가 가장 가까운 올레 장소 하나를 추가한다. 올레 후보는 `cat === 'hike'`와 `id.startsWith('olle-')`로 찾는다.
5. `walk === 'hike'`이면 한라산 플래너 안내를 표시한다. 이 분기 자체가 일반 추천 코스에 한라산을 삽입하거나 등반 가능성을 판정하지는 않는다.
6. 첫 장소를 고정하고 나머지를 직선거리 기준 최근접 이웃 순서로 정렬한다. 도로·대중교통 이동시간이나 전체 동선의 전역 최적해를 계산하는 단계는 아니다.
7. 이동수단은 코스빌더에 넘기는 `CourseDraft.local`에 반영한다. 이 설문 함수에서 장소 후보 점수나 올레 추가 여부를 계산할 때 이동수단을 사용하지는 않는다.

따라서 현재 설문을 AI 성격 진단, 학습된 개인 행동 예측 또는 제약 충족이 검증된 일정 생성으로 설명하지 않는다. `popRank`의 기반이 되는 집계 방문량도 개인 사용자의 선호 정답이 아니다.

## 4. 별도로 존재하는 AI 여행 비서와 검색

기존 서비스에는 설문 외에 **이미 대화형 AI 여행 비서와 자연어 장소 검색이 구현돼 있다.** 새 기획은 이 흐름을 출발점으로 삼는다.

- API 연결: [`server.ts:4554`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L4554)의 `/api/course/search`, `/api/assistant` 및 상태 조회 경로.
- 대화 해석: [`assistant.ts:90`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L90)에서 날짜·박수·장소 요구 묶음·제외 장소·응답 상세도를 구조화한다.
- 서버 후보 산정: [`assistant.ts:198`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L198)에서 요구별로 기존 장소를 `rank()`로 평가한다.
- 답변 구성: [`assistant.ts:109`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L109)의 프롬프트는 후보·현재 코스에 존재하는 장소만 선택하고 목록에 없는 가격·메뉴·영업 여부를 만들지 않도록 지시한다.
- 반환 검증: [`assistant.ts:229`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L229)에서 동작과 추천 항목의 ID를 후보·현재 코스 집합으로 제한한다. 코스 동작은 `none`, `replace`, `add`, `remove`다.
- 모델 미설정·오류·한도 초과 시 [`assistant.ts:174`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts#L174)의 키워드 후보 목록으로 대체한다.

코드에는 Gemini/Vertex AI 호출, 모델 선택 환경변수, 타임아웃과 대체 모델 경로가 있다. 이는 구현 존재의 확인이며 해당 모델명·계정·인증·현재 공급자 제공 여부나 품질을 이번 작업에서 검증한 것은 아니다. 비밀값을 조회하거나 이 저장소로 복사하지 않았다.

자연어 검색의 점수는 설문 점수와 다르다. [`placesearch.ts:35`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/placesearch.ts#L35)는 분류·제외어를 필터링하고, 권역·키워드·실내 여부·자연·배지·접근성·영업시간·인기도를 가감점한다. 접근성은 확인된 표식이 있으면 가점, 없으면 감점이며 필수 배제 조건이 아니다. 영업시간이 없으면 해당 시간 점수를 적용하지 않는다. [`rank():55`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/placesearch.ts#L55)는 권역 내 후보가 3개 미만일 때 권역 조건을 풀어 다시 점수를 계산한다.

확장 시에는 **선호 점수와 반드시 지켜야 할 제약을 분리**해야 한다. 특히 접근성·특정 지역·시간 조건을 필수로 요청한 경우에는 기존 감점·자동 완화만으로 조건 충족을 주장할 수 없다. ID 필터는 존재하지 않는 장소 참조를 줄이지만 자유 서술의 모든 사실을 검증하는 장치는 아니므로 근거 문장·유효시각 검사도 필요하다.

## 5. 재사용할 수 있는 데이터와 계약

[`CoursePlace` 스키마](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/shared/types.ts#L434)는 `id`, `name`, `cat`, `kind`, 위·경도, `zone`, 권장 체류시간 `stay`, `hours/open/close`, `indoor/nature`, `pop/popRank`, `badges`, `auto`, 연락·웹 정보, 선택적 `bf`·`drama`·`hub` 정보를 포함한다. 이를 새 장소 식별 체계와 무관하게 복제하기보다 기존 ID를 연결 키로 보존하는 것이 적절하다.

고정 커밋의 [`prototype/data/jeju_tour_data.json`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/data/jeju_tour_data.json)을 실제 JSON으로 읽어 확인한 수는 다음과 같다.

| 원본 배열 | 레코드 수 | 해석 범위 |
| --- | --- | --- |
| `pois` | 7,561 | 원본 POI 수. 중복 제거·품질·라이선스·영업상태 검사 완료 수가 아님 |
| `olle` | 29 | 원본 올레 레코드 수 |
| `trails` | 6 | 원본 탐방로 레코드 수 |

이 숫자는 코스빌더의 최종 추천 가능 장소 수가 아니다. [`course.ts:112`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/course.ts#L112)에 포함 필터가 있고, [`buildCoursePlaces():177`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/course.ts#L177)에서 정규화·추가 자료 연결이 이루어진다. 실제 API 반환 수는 런타임에서 별도 확인해야 한다.

JSON의 `sources`는 관광객 차량 도착 집계, 카카오맵 수집, OpenStreetMap, 제주올레 공식 자료, Halla-GeoData/Copernicus 자료를 출처로 기록한다. **출처 메타데이터가 있다는 것과 신규 연구·재배포·모델 학습 이용권이 확인됐다는 것은 별개**다. 원본 수집 방식과 제공자의 조건을 재검토해야 한다.

추가로 원본 저장소의 [`docs/data-downloads-and-api-keys.md`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/docs/data-downloads-and-api-keys.md), `prototype/data/public-csv/`, `prototype/data/hub-source/`, `prototype/data/hub-extra/`에는 CSV/API 연동과 기준일·해시에 대한 자료가 있다. 새 데이터 계획에서는 이들 자료의 실제 파일과 품질을 먼저 조사해 중복 수집을 줄인다. 현재 첨부 Excel의 행 목록과 일치한다고 간주하지 않으며, 카탈로그 템플릿에는 임의로 복사하지 않는다.

## 6. 기존 흐름과 신규 기획의 연결

| 현재 구현 | 확장할 내용 | 호환성·평가 기준 |
| --- | --- | --- |
| 4문항 설문 | 기존 답변 키를 보존하면서 목표 테마, 필수 조건, 불확실성을 구조화 | 기존 4문항 결과를 규칙 기준선으로 고정하고 변경 효과 비교 |
| 테마 단일 선택 | 복수 테마 가중치와 사용자 확인을 거친 이번 여행의 취향 표현 | 보호 특성이나 집계 인구통계로 개인 성향을 단정하지 않음 |
| `topOf()` 후보 선정 | 근거 기반 후보 검색·다양성·권역·시간 조건 검증 | 같은 장소 ID·후보 풀·평가 질의로 기준선과 비교 |
| `CoursePlace`·코스빌더 | 필드별 출처·기준일·제약 검증 결과를 덧붙이는 어댑터 | 기존 저장 코스와 ID를 유지하고 필요한 새 필드만 버전 관리 |
| AI 비서의 해석→후보→답변 | 논문 검토 후 LAG 적용 지점과 필요한 자료 확정 | LAG를 구현됐다고 표시하지 않으며 기존 Gemini 경로·RAG 비교 경로와 분리 |
| 후보 ID 허용 목록 | 근거 문장, 운영시간 유효성, 필수 조건 위반 검사 추가 | 목록에 있는 장소라도 근거 없는 상세 설명은 허용하지 않음 |
| 코스 변경 동작 | 추천 근거·대안 제시 후 기존 빌더에서 확인·수정 | 사용자 코스를 바꾸는 시점과 자동 제안 시점을 구분 |
| 키워드 대체 경로 | 실패 원인·축소된 기능·근거 시점을 명시 | 오류를 정상 AI 추천처럼 표시하지 않음 |

LAG는 사용자가 지정한 방법이다. 처음 지정된 식별자 `2504.09277`은 2026-11-18 확인 결과 SynthTRIPs(평가셋 생성 틀)였고, 사용자 결정에 따라 LAG는 `2508.05509` Logic-Augmented Generation을 뜻한다([19 문서](19-agent-step2-data-foundation.md)). 현재 검토한 원본 앱 소스·문서에서 두 논문 식별자나 `LAG`를 찾지 못했다. 따라서 기존 AI 비서를 LAG 구현이라고 부르거나 LAG를 RAG의 오타로 해석하지 않는다. Llama 계열 모델을 대체 후보로 검토하는 경우도 모델 라이선스·한국어 품질·구조화 출력·자원·지연·비용을 별도로 평가해야 한다.

## 7. 배포·검증 상태와 남은 확인

[`prototype/vercel.json`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/vercel.json)은 서울 `icn1`, `npm run build`, `dist/public`, API 함수 제한 120초와 라우팅을 설정한다. [`prototype/package.json`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/package.json)에는 React/TypeScript/Express 기반 빌드·타입 검사·Playwright 검사 명령이 있다. [`leisure-theme.spec.cjs`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/tests/leisure-theme.spec.cjs)에는 4문항 응답 후 4곳이 나타나는 시나리오 등의 테스트가 존재한다. 이 파일들의 존재를 이번 실행에서 통과한 테스트 수로 보고하지 않는다.

남은 확인은 다음과 같다.

1. `jeju-tour-six.vercel.app`에 대한 환경 네트워크 접근이 허용되면 현재 화면과 읽기 전용 API 응답을 확인한다. 현재 차단은 서비스 자체 장애의 증거가 아니다.
2. 배포된 커밋과 검토한 커밋의 일치 여부, 현재 API·모델·외부 데이터 가용성을 별도로 확인한다. 저장소의 과거 배포 기록이나 변수 등록 기록만으로 현재 사용 가능을 주장하지 않는다.
3. 사용자가 언급한 PDF·Excel 원본을 확보한다. 두 참고 체크아웃에서 `.pdf`, `.xls`, `.xlsx` 파일은 발견하지 못했다.
4. 논문 원문을 확인해 LAG 정의·입력·절차·평가 조건을 페이지와 함께 정리한다.
5. 기준선 테스트를 실제 실행할 별도 개발 단계에서 기존 저장소의 의존성과 잠금파일을 존중하고, 설문·검색·대화 비서·일정 제약을 각각 검증한다.

현재 확인한 결론은 **기존 서비스의 설문·장소 데이터·코스빌더·AI 비서를 재사용할 구체적인 출발점이 있다**는 것이다. 운영 상태, 논문 재현, 첨부 자료 분석 또는 신규 AI 추천 성능이 검증됐다는 결론은 아니다.
