# 기존 Vercel 앱에 연결하는 MCP 서버·에이전트 통합 계획

## 1. 목표와 현재 확인 범위

통합 대상은 사용자가 지정한 **`https://jeju-tour-six.vercel.app/`**이다. 기존 화면·장소 ID·코스 저장·회원 인증을 재사용하면서 Ollama 기반 추론과 논문 LAG 검토 결과를 연결한다. 별도의 데모 사이트를 만드는 것으로 기존 서비스 통합을 대신하지 않는다.

이 문서는 전체 구현·배포 계획이다. 이 중 읽기 전용 3개 도구와 HTTP MCP를 분석 저장소에 구현해 로컬 검증했으며, 완료 범위는 [13 문서](13-next-steps-and-mcp-implementation.md)에 기록했다. 운영 사이트 변경, 외부 상시 서버 생성, 모델 설치, 운영 인증 설정 변경 또는 유료 API 호출은 수행하지 않았다. 현재 사이트 접속은 환경 프록시의 403으로 차단돼 런타임 상태와 배포 커밋이 확인되지 않았다. 소스 근거는 `OREUM-IT/jeju-tour`의 고정 커밋 **`b0602a175c1d08959b7187da2b29ac450b52e0ba`**다.

LAG는 사용자가 지정한 논문에서 정의한 방법이다. 본문을 확보하지 못했으므로 LAG의 내부 처리·학습 요구·검색 구조를 가정하지 않는다. MCP는 도구 연결 규약이고 Ollama는 모델 실행 수단이며, 둘을 도입하는 것만으로 LAG가 구현되는 것은 아니다. 논문 검토와 모델 실행 계획은 [09 문서](09-ollama-lag-feasibility.md)와 함께 확정한다.

## 2. 소스에서 확인한 현재 구조

| 영역 | 현재 소스 근거 | 통합에 미치는 영향 |
| --- | --- | --- |
| 기존 MCP | [`server.ts:2770`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L2770)의 `McpClient` + `StdioClientTransport` | 앱 서버가 ODsay MCP의 **클라이언트**다. 공개 HTTP MCP 서버가 이미 있다고 볼 수 없음 |
| MCP 실행 파일 | `../tools/odsay-mcp/dist/index.js`를 Node 자식 프로세스로 실행 | 현재 체크아웃에 `tools/`가 없어 서버 소스·도구 스키마를 확인하지 못함. 주석의 경로만으로 재사용 완료를 주장하지 않음 |
| MCP 도구 호출 | [`server.ts:3632`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L3632)의 `key_status`, [`server.ts:3770`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L3770)의 `transit_route` | `transit_route`에는 출발/도착 좌표, 날짜·시간, `max_paths:3`, `bus_only:true`, 시간표 포함 여부를 전달. 현재 응답은 실시간 도착이 아니라고 명시 |
| Vercel에서 MCP | [`transitTool():2795`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L2795)는 `VERCEL` 환경 또는 실행 파일 부재 시 503 | 현재 stdio 경로를 그대로 Vercel에서 활성화할 수 없음. 기존 ODsay 중지·미지원 상태를 새 설계가 자동 해제하지 않음 |
| AI 비서 | [`assistant.ts`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/assistant.ts)의 요청 해석 → 규칙 후보 평가 → 답변 구성 | 새 추론 엔진을 넣을 구체적인 연결점. 후보 ID 검증과 키워드 대체 경로 보존 |
| 공급자 결합 | [`placesearch.ts:161`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/placesearch.ts#L161)의 Vertex 요청과 `gemini<T>()`를 비서가 직접 호출 | 현재 공급자 공통 인터페이스가 완성돼 있지 않음. Ollama 응답을 기존 Gemini 함수로 오인해서 연결하지 않음 |
| 인증 | [`member-auth.ts`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/member-auth.ts)의 Bearer 토큰 → Supabase `/auth/v1/user` → `res.locals.userId` | 클라이언트가 보낸 `userId`를 신뢰하지 않는 기존 원칙 재사용 |
| 보호 경로 | [`course-plans.ts`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/course-plans.ts)의 `requireMember`, 사용자별 SQL 조건, `no-store` | 코스 저장·조회는 회원 소유권을 유지. DB 직접 연결에 RLS만 있다고 안심하지 않고 쿼리의 소유자 조건도 유지 |
| 현재 비서 인증 | [`server.ts:4557`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/server.ts#L4557)의 `/api/assistant`와 `/api/course/search`에는 `requireMember`가 연결돼 있지 않음 | 기존 API가 인증된 작업 큐나 개인별 사용량 통제를 제공한다고 가정하지 않음. 신규 경로에 명시적 정책 필요 |
| Vercel 진입·제한 | [`api/index.js`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/api/index.js)는 Express 앱을 내보냄. [`vercel.json`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/vercel.json)은 `icn1`, 함수 `maxDuration:120` 설정 | 120초는 이 저장소의 현재 설정이며 모든 Vercel 제품의 보편적 제한이라는 뜻이 아님. 배포 플랜·실제 제한 재확인 필요 |
| 프런트엔드 적용 | [`Assistant.tsx:126`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/features/course/Assistant.tsx#L126)는 사용자가 버튼을 눌러 코스 동작 적용 | 새 엔진도 추천 생성과 실제 코스 변경 시점을 분리하고 기존 적용 UX 유지 |

잠금파일에서 실제 해석된 버전은 `@modelcontextprotocol/sdk 1.32.1`, `zod 4.6.7`이다. SDK 1.32.1 배포 아티팩트의 SHA-512를 검증하고 소스를 정적으로 확인한 결과 `LATEST_PROTOCOL_VERSION`은 `2025-11-25`이며 해당 버전이 지원 목록에 포함된다. 아티팩트를 설치하거나 실행한 검증은 아니다. 출처의 npm `gitHead`는 `ff07b001194fe60ee9deb2121cf119057565796d`다.

현재 stdio 어댑터는 부모 프로세스의 환경변수를 자식에 전달한다. 새 MCP 서비스에서는 필요한 변수만 명시적으로 전달하도록 설계하며 이 방식을 그대로 복제하지 않는다. 기존 SDK 의존성의 존재는 원격 MCP 인증·서버·도구 정의가 구현됐다는 증거가 아니다.

## 3. 권장 배치: 기존 앱은 유지하고 추론 실행을 분리

공유 MCP는 OpenAI·Claude·Ollama 어댑터에서 함께 사용한다. 공급자별 터널을 필수로 두지 않으며 [다중 모델·터널·DB·비용 설계](11-multi-provider-tunnels-database-and-cost.md)의 실행 모드와 영속 작업 구성을 함께 적용한다.

`기존 웹 → Vercel 인증 API → 외부 Agent API·영속 작업 큐 → Worker → 사설 Ollama / HTTP MCP 도구 서버 → 검증 결과 → 기존 코스빌더`

배포 경계와 작업 흐름의 시각 설계도는 [09 문서](09-ollama-lag-feasibility.md)의 두 그림을 따른다. 각 호스트·포트·비밀값은 후속 구축 단계에서 정한다.

- **Vercel BFF:** 기존 앱의 동일 출처 API로 인증·입력 검증·사용량 제한·응답 호환을 담당한다. 동기 경로는 우선 전체 25초 이하를 설계 예산으로 두고, 그 안에 완료되지 않을 작업은 외부 큐에 넘긴다. 이는 측정 전 제안값이다. GPU 모델 실행·장시간 작업·로컬 자식 프로세스 생존에 의존하지 않는다.
- **외부 에이전트 API와 워커:** 작업을 영속 큐에 넣고 재시작·중복 실행·취소를 다룬다. 워커는 MCP 클라이언트이자 모델 호출 주체다. 사용자별 상태는 외부 저장소에 두며 메모리 Map만으로 작업 생명주기를 관리하지 않는다.
- **Ollama:** 추론 실행에 집중한다. 브라우저에서 직접 접근하지 않으며 공개 무인증 포트를 열지 않는다. GPU·모델 메모리·동시 실행 수는 측정 결과로 정하고 세부 LAG 처리는 원문 확인 후 배치한다.
- **MCP 서버:** 허용한 도구·리소스의 스키마, 권한, 최신성, 실패 형식을 제공한다. 모든 REST API를 자동으로 도구화하거나 임의 URL·SQL·셸 실행 도구를 제공하지 않는다.
- **기존 도메인 서비스 재사용:** 처음에는 검증된 기존 조회 API에 서버 어댑터를 연결할 수 있다. 앱 비서가 도구를 호출하고 도구가 다시 `/api/assistant`를 호출하는 순환은 만들지 않는다. 이후 순수 조회·검증 모듈을 공유 패키지로 분리하되 기존 ID·분류·날짜 규칙을 유지한다.

ODsay 서버 원본을 확보하면 도구 스키마와 쿼터 정책을 감사한 뒤 외부 워커에서 기존 stdio 서버를 관리하거나 원격 HTTP 어댑터로 감쌀 수 있다. 현재는 파일 부재·기존 사용 중지 기록·공급자 접근 검증이 해소되지 않았으므로 `transit.route`를 활성화할 전제로 계획하지 않는다.

## 4. MCP 전송·세션 설계

**초기 통합 기준은 기존 SDK `1.32.1` + 지원 프로토콜 `2025-11-25`로 고정한다.** [해당 버전의 전송 사양](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)에 맞춘 외부 HTTPS Streamable HTTP를 사용하고, 우선 세션 없는 짧은 JSON POST 도구 호출로 구성한다. 모델이 아닌 **워커가 MCP 클라이언트**다. Ollama에는 모델 메시지와 허용된 도구 결과만 제공한다. 브라우저는 초기 배치에서 MCP 서버에 직접 연결하지 않는다.

“서버리스이므로 상태나 스트리밍이 불가능하다”고 단정하지 않는다. 짧은 요청을 위한 stateless HTTP 구성이 가능하고, 세션·이벤트 재개가 필요하면 외부 상태 저장소와 연결 라우팅을 함께 설계할 수 있다. 다만 특정 함수 인스턴스의 메모리, stdio 자식, 열린 소켓이 다음 요청까지 유지된다고 가정하지 않는다. Vercel에서 짧은 MCP 프록시를 구현할 경우에도 인증·타임아웃·헤더 전달·응답 크기를 검증하며 장시간 추론 완료를 그 연결에 종속시키지 않는다.

아래 전송·세션 항목은 모두 **2025-11-25 규격 범위**다. 현재 최신 MCP/SDK v2의 수명 규칙과 혼합하지 않는다. 잠금파일은 그대로 유지하며 새 서버와 기존 클라이언트의 실제 협상·응답으로 다음을 검증한다.

1. `initialize`, 버전 협상, `notifications/initialized`, `tools/list`, `tools/call`의 스키마와 콘텐츠 형식. 세션 없는 서버에서도 정상 v1 클라이언트의 초기화 순서를 생략하지 않는다.
2. `POST /mcp`의 JSON 응답을 기본으로 한다. 후속 단계에서 이 규격의 SSE·선택적 `GET` 이벤트 연결·세션을 구현할 경우에만 종료와 재개 동작을 추가한다. 첫 버전에 모두 필요하다고 간주하지 않는다.
3. `Mcp-Session-Id`는 인증 정보가 아니다. 모든 HTTP 요청에서 액세스 토큰과 소유권을 검사하고 세션 ID 탈취만으로 접근할 수 없게 한다.
4. 지원 버전의 프로토콜 헤더, `Accept`, 콘텐츠 유형, 허용 메서드를 검사한다. 일반 HTTP 프록시가 이벤트 스트림을 임의 버퍼링하거나 자르는지 실제 배포에서 확인한다.
5. 세션 만료·재초기화·네트워크 단절 시 **도구 쓰기 요청을 자동 재실행하지 않는다**. 필요한 요청은 별도 멱등성 키와 작업 조회로 복구한다. 해당 규격의 요청 취소 알림과 애플리케이션의 영속 작업 취소 API는 별개로 구현한다.

초기 서버는 고정 v1 예제처럼 POST마다 서버·transport를 생성하고 `sessionIdGenerator:undefined`를 사용한다. stateless transport 한 개를 여러 요청에서 재사용하지 않는다. 읽기용 데이터 캐시는 별도 계층에서 재사용할 수 있지만 사용자 인증 컨텍스트는 요청마다 검증한다. 초기 구현은 선택적 GET 이벤트 연결과 세션 삭제를 제공하지 않고 해당 메서드에 405를 반환하는 범위로 시험한다.

현재 최신 2026-07-28 규격/SDK v2는 프로토콜 세션·독립 GET 스트림·연결 단절과 취소·재개 규칙이 기존 규격과 달라 별도의 마이그레이션 작업으로 다룬다. 최신 예제의 동작을 위 설계에 일부만 끼워 넣지 않는다. 현재 v2를 대상으로 하는 Vercel `mcp-handler`도 기존 v1 프로젝트에 바로 추가하지 않는다. 업그레이드 시 Node 요구 버전·인증·전송·취소 계약을 함께 시험한다. 고정 출처는 11절에 정리했다.

## 5. 도구 계약: 작은 조회 단위부터 시작

아래 도구명·입출력은 **제안**이다. 현재 운영 MCP 도구 목록이 아니다. JSON Schema에 `additionalProperties:false`, 길이·개수·좌표·날짜·enum 범위를 적용하고 날짜는 `Asia/Seoul` 기준을 명시한다. 모든 도구는 요청별 제한 시간과 결과 크기 한도를 갖는다.

| 제안 도구 / 권한 | 주요 입력 | 구조화 출력·검증 | 재사용 근거·제한 |
| --- | --- | --- | --- |
| `places.search` / `places:read` | 테마·권역·키워드·후보 수 ≤50, 명시적 필수 조건 | 기존 `placeId`, 후보 점수, 필드 근거, `unknownConstraints`, 자료 버전 | `CoursePlace`와 `rank()` 재사용. 기존 가감점으로 필수 제약 통과를 대신하지 않음 |
| `places.get` / `places:read` | 유효한 ID 최대20개 | 이름·좌표·분류·영업·접근성의 값/출처/기준일/미확인 상태 | `/api/course/places`, `/api/course/bf/:id` 등 어댑터. 새 ID 임의 생성 금지 |
| `evidence.get` / `evidence:read` | 허용된 `evidenceId`, 필드 목록 | 인용 가능한 구절, 출처 URL, 수집일·유효기간·이용권 범위 | 새 출처 카탈로그 필요. 임의 외부 URL fetch 기능으로 바꾸지 않음 |
| `weather.get` / `weather:read` | 제주 범위 좌표·여행 날짜 | 관측/예보 구분, 유효시각, 출처, 범위 밖·조회 불가 상태 | `/api/weather`, `/api/kma/*` 검토 후 연결. 먼 미래 날짜의 실시간 예보를 만들어내지 않음 |
| `routes.estimate` / `routes:read` | 출발/도착 ID 또는 검증 좌표, 이동수단, 출발 시각 | 거리·시간·공급자·조회 시각·예측 여부·대체 이유 | `/api/course/leg`, `/api/route/plan` 재사용. 도로/직선·실시간/추정 구분 |
| `constraints.check` / `constraints:read` | 후보 ID, 날짜·시간, 이동·접근성·예산 등 명시 조건 | 조건별 `pass/fail/unknown/conflict`, 근거와 재확인 필요 | 새 검증 계층. 미확인·상충을 통과로 처리하지 않고 상충한 근거·조건을 보존 |
| `itinerary.propose` / `plans:propose` | 검증된 후보 ID, 일정 범위, 제약, 기준 초안 버전 | 저장하지 않은 코스 제안, 근거·검증 결과·계산기 버전 | 제한 시간 안의 일정 계산기만 호출. 전체 Agent 루프를 재호출하거나 소유 코스를 덮어쓰지 않음 |
| `plans.read` / `plans:read:self` | 본인 `planId` | 소유권 확인된 초안과 버전 | 기존 회원 코스 저장 모듈 재사용. 도구 인자로 `userId`를 받지 않음 |
| `plans.save` / `plans:write:self` | 승인된 제안 ID, 기대 버전, 멱등성 키 | 새 저장 버전 또는 충돌 결과 | 후속 단계에서만 노출. 초기에는 기존 앱 저장 버튼이 수행하며 모델에 쓰기 권한을 주지 않음 |

`readOnlyHint`·`idempotentHint` 등 MCP 주석은 클라이언트에 주는 힌트이며 권한 검사를 대체하지 않는다. 읽기 도구도 유료 조회·쿼터를 사용할 수 있어 사용자별·공급자별 호출 예산을 검사한다. 입력·출력 검증은 모델 프롬프트의 문구가 아니라 서버 코드에서 수행한다.

`itinerary.propose`의 계산이 도구 호출 예산을 넘으면 제한 사유를 반환하고 Worker가 앱 작업의 다음 단계로 분리한다. MCP 도구가 같은 Agent를 재귀 호출하여 작업을 중첩 생성하지 않도록 실행 책임을 Worker 한 곳에 둔다.

공통 `structuredContent`에는 `schemaVersion`, `requestId`, `status`, `data`, `evidence`, `warnings`, `retrievedAt`, `validUntil`, `dataVersion`을 둔다. `status`는 `ok/partial/unavailable/requires_confirmation` 등 실제 상태를 표현한다. 도구 실행 실패는 적절한 MCP 도구 오류와 안전한 오류 코드를 반환하고, JSON-RPC 형식 오류와 구분한다. 비밀값·서명 URL·공급자 원문 인증 오류는 그대로 응답하지 않는다.

검색 결과·외부 설명문·모델 출력은 비신뢰 데이터로 처리한다. 근거 문장에 포함된 명령이 권한·도구 선택 정책을 바꾸지 못하도록 시스템 지시와 분리하고, URL·호스트는 서버 허용 목록으로 제한한다.

## 6. 인증·위임·소유권

### 기존 웹에서 시작하는 권장 경로

1. 브라우저는 기존 Supabase 세션으로 동일 출처 `/api/agent/*`를 호출한다. [`client/lib/api.ts`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/client/lib/api.ts)의 토큰 부착 흐름을 재사용한다.
2. Vercel은 `requireMember` 또는 동등하게 검증된 인증 계층을 거쳐 사용자 ID를 결정한다. 현재 코드처럼 Supabase 사용자 조회로 확인하거나, 이후 JWT 로컬 검증을 택하면 프로젝트의 신뢰된 JWKS·알고리즘·issuer·기대한 audience·만료를 모두 검증한다. 단순 디코딩으로 대체하지 않는다.
3. Vercel은 검증된 주체·작업 범위로 **짧은 수명의 내부 위임 토큰**을 발급한다. `sub`, `iss`, 대상 서비스에 고정된 `aud`, `exp`, `jti`, 허용 scope, 필요하면 `jobId`·자료 접근 범위를 포함한다. 수명은 작업 제출에 필요한 분 단위로 설계하고 장시간 작업 권한은 워커의 서버 자격과 저장된 작업 소유권으로 관리한다.
4. 외부 에이전트 API는 내부 issuer·audience·서명·만료·scope를 검증한다. 브라우저가 보낸 `X-User-Id`나 본문 `ownerId`로 권한을 부여하지 않는다.
5. 워커가 MCP 도구를 호출할 때는 **그 MCP 서버를 audience로 하는 별도 토큰**을 사용한다. 앱·에이전트 API·MCP·외부 공급자 간에 토큰을 그대로 통과시키지 않는다. Supabase 프로젝트 토큰을 임의 MCP 서버용 토큰처럼 취급하지 않는다.

작업 조회·취소·코스 읽기·저장은 매번 `tenant/user/job` 소유권과 scope를 확인한다. 임의의 `jobId`를 안다는 이유로 결과를 볼 수 없게 한다. RLS가 있는 테이블도 서버 DB 역할이 우회할 수 있으므로 서버 쿼리의 소유자 조건과 테스트를 유지한다. 관리자 서비스 자격은 모델·브라우저·도구 인자로 전달하지 않는다.

초기 신규 장시간 추론·개인 코스 도구는 로그인 사용자 대상으로 제공한다. 기존 비로그인 설문·공개 장소 조회·기존 비서 경로는 그대로 유지한다. 추후 비로그인 작업이 필요하면 서명된 짧은 수명의 익명 세션과 별도 낮은 한도·결과 접근 권한을 설계하며 공개 `jobId`만으로 결과를 노출하지 않는다.

### Origin·CSRF·외부 MCP 클라이언트

- 동일 출처 웹 요청은 실제 Production 출처 `https://jeju-tour-six.vercel.app`를 허용한다. Preview는 명시적으로 승인한 호스트만 별도 환경에서 허용하며 `*.vercel.app` 전체를 신뢰하지 않는다. 자격증명 포함 요청에 CORS `*`를 사용하지 않는다.
- 쿠키 인증을 추가하면 `Secure`, `HttpOnly`, 적절한 `SameSite`와 CSRF 토큰·Origin 검증을 적용한다. 현재처럼 Authorization 헤더 토큰을 사용하는 경우에도 출처 검증과 XSS 방어가 필요하며, CSRF 토큰만으로 XSS나 토큰 탈취를 해결할 수는 없다.
- 원격 MCP 서버는 브라우저 Origin이 있으면 정확한 허용 출처를 검사한다. Origin이 없는 서버 간 클라이언트는 유효한 인증·scope와 필요 시 네트워크 정책으로 허용한다. Origin이 없다는 사실 자체는 인증이 아니다.
- 향후 제삼자 MCP 클라이언트에 공개할 경우 현재 내부 토큰 방식과 별도로 공식 MCP 인증 규격의 보호 리소스 메타데이터·발급자·resource/audience 검증·OAuth 클라이언트 정책을 설계한다. 구현하지 않은 OAuth 지원을 문서만으로 완료 처리하지 않는다.

## 7. 기존 API 호환과 긴 작업의 수명

현재 `/api/assistant`는 한 요청 안에서 완료되는 `AssistantReply`를 반환한다. `assistant.ts`는 전체 110초, 대체 모델을 위한 8초 여유, 해석/답변의 별도 제한 시간을 둔다. 이 값을 새 Ollama 실행의 성능 보장으로 쓰지 않는다. 모델 적재·대기열·추론 시간이 현재 함수 예산을 넘을 수 있어 신규 경로는 작업 형태로 설계한다.

| 제안 앱 API | 동작 |
| --- | --- |
| `POST /api/agent/jobs` | 스키마·인증·한도·멱등성 검사 후 외부 큐에 제출. `202`와 `jobId`, 상태 조회 경로, 스키마 버전 반환 |
| `GET /api/agent/jobs/:id` | 본인 작업 상태·진행 단계·마지막 이벤트 순번·완료 결과 조회. 민감 응답은 `Cache-Control:no-store` |
| `GET /api/agent/jobs/:id/events` | 선택 기능. 지원 환경에서 진행 이벤트 SSE 제공; 폴링과 같은 작업 상태를 기반으로 함 |
| `POST /api/agent/jobs/:id/cancel` | 본인 작업의 취소 요청 기록. 완료·취소된 작업에 중복 호출해도 같은 최종 상태 반환 |
| 기존 `/api/assistant` | 초기에는 기존 엔진 유지. 새 UI가 기능 플래그에 따라 신규 경로를 사용하며 롤백 시 기존 응답으로 복귀 |

제안 작업 상태는 `queued → running → completed/failed/cancelled/expired`다. 취소 요청과 실행 종료 사이에는 `cancellation_requested`를 표시한다. **이 작업 API는 MCP 프로토콜의 내장 작업 기능이 아닌 별도 애플리케이션 REST 계약이다.** 앱의 상태 조회 연결 종료는 영속 작업 취소와 동일하지 않게 설계한다. 앱 SSE 연결이 끊기거나 사용자가 페이지를 새로고침해도 저장된 작업 ID로 권한 확인 후 폴링·이벤트 재개가 가능하게 한다. 실제 이벤트 재생을 구현할 때에만 애플리케이션 이벤트 ID와 재개 커서를 제공하며, 이를 최신 MCP의 세션·재개 지원이라고 부르지 않는다.

작업은 입력 해시, 사용자, 엔진·모델 digest, 데이터 버전, 설정 버전, lease/heartbeat, 단계 체크포인트, 결과·오류, 만료 시각을 저장한다. 큐가 최소 한 번 전달할 수 있음을 전제로 동일 단계 재실행을 안전하게 만든다. 취소는 대기열 제거 또는 실행 워커의 `AbortSignal` 전파로 협력 처리하며, 이미 끝난 외부 호출·확정된 저장을 되돌렸다고 표시하지 않는다.

최초 UX는 폴링을 기본으로 하고 짧은 간격에서 시작해 상한 있는 지수 간격과 jitter를 적용한다. 폴링·재개·취소 요청도 인증과 속도 제한을 받는다. Vercel이 장시간 SSE를 지원하더라도 연결 수명과 작업 수명을 분리한다.

동기/비동기 경로는 접수 시 결정하며 무거운 요청은 바로 작업으로 접수한다. 이미 실행한 동기 요청이 시간 초과됐다고 같은 요청을 새 작업으로 무조건 중복 실행하지 않는다. 같은 멱등성 키·작업 기록으로 기존 실행을 조회하거나 확인된 중단 지점에서 재개한다. DB 기록과 큐 발행 사이에는 트랜잭션 outbox 또는 동일한 내구성 보장을 설계하고, 202는 복구 가능한 접수가 기록된 뒤 반환한다. `(owner, endpoint, idempotency_key)`에 대한 유일성 제약과 입력 해시 대조로 동시 중복 제출도 막는다.

완료 결과는 기존 `reply`, `action`, `places` 의미를 유지하는 호환 어댑터로 전달한다. [`types.ts:605`](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/src/shared/types.ts#L605)의 현재 `engine`은 `'gemini' | 'keyword' | 'none'`이다. Ollama를 `gemini`로 위장하거나 새 문자열을 기존 클라이언트에 무조건 보내지 않고 API 버전·공유 타입·새 UI를 함께 배포한다. `action`은 기존처럼 사용자가 적용 버튼을 누르기 전에는 초안을 변경하지 않는다. 생성 시점과 현재 코스 버전이 달라지면 충돌을 알리고 재검토한다.

## 8. 멱등성·실패·사용량 처리

- **작업 제출:** `Idempotency-Key`를 사용자·엔드포인트·정규화한 입력 해시로 묶는다. 같은 키·같은 입력은 기존 작업을 반환하고 다른 입력이면 `409`다. 키 저장 기간과 작업 보관 기간을 문서화한다.
- **쓰기:** `plans.save`는 사용자·제안 ID·기대 버전·멱등성 키를 원자적 트랜잭션으로 검사한다. 낡은 제안은 `409`, 이미 적용한 같은 요청은 기존 결과를 반환한다. 모델이 만든 `approved:true`로 사용자 승인을 대체하지 않는다.
- **조회 재시도:** 전송 오류·일시적 429/5xx에 한해 작은 횟수 제한과 backoff/jitter·`Retry-After`·남은 전체 예산을 적용한다. 인증·권한·스키마 오류는 무작정 재시도하지 않는다.
- **모델 재시도:** Ollama 네트워크 실패, 구조화 출력 오류, GPU 자원 부족을 구분한다. 제한된 재시도 후 제약을 완화하지 않은 규칙 기반 대체 결과 또는 실패를 반환한다. 제공자 변경·사용량·외부 전송 범위를 사전에 설정하고 응답에 사용 엔진과 축소된 기능을 표시한다.
- **공유 한도:** 기존 `usage`·모델 차단 Map은 인스턴스 로컬 상태다. 새 경로에서는 사용자별·전체·공급자별 한도와 동시 실행 제한을 외부 저장소/큐로 집계한다. 로그는 request/job/tool ID와 지연·오류 코드 중심으로 남기고 토큰·원문 개인 대화·연락처를 기본 수집하지 않는다.

기존 Gemini 구현은 `thinking` 설정 관련 400일 때 설정을 뺀 한 번의 재시도, 모델별 또는 기본 모델의 10분 차단, 비서의 대체 모델 경로를 갖는다. 이를 범용 재시도 라이브러리로 간주하지 않는다. 제안 `ModelProvider.generateStructured()`에는 입력 스키마·모델 버전·최대 토큰·전체 deadline·취소 신호를 전달하고, 반환 JSON을 런타임 스키마로 검증한다. Ollama의 구조화 출력·도구 호출 지원 여부는 선택 모델별로 실제 시험한다.

## 9. 단계별 적용과 롤백

1. **기준선 고정:** 현재 설문·Gemini 비서·검색·코스 적용·저장 계약과 테스트를 보존한다. 원본 LAG 검토 전에는 `method=lag`로 표시하지 않는다. `engine`은 Gemini/Ollama 등 실행 공급자, `method`는 추천 방법을 구분한다.
2. **오프라인 어댑터:** 공개·허용된 고정 데이터로 `places.search/get`, `constraints.check`부터 구현한다. 악성 도구 입력, 다른 사용자 ID, 누락 근거, 중복 제출, 취소를 검증한다.
3. **개발 환경 연결:** Vercel Preview와 별도 에이전트 개발 서버를 연결한다. Production 사용자 토큰·개인 자료·운영 쓰기를 복제하지 않는다. TLS·audience·정확한 Origin·시간 제한을 확인한다.
4. **읽기 전용 shadow:** 허용한 평가 요청을 기존 경로와 비교하되 결과를 코스에 적용하지 않는다. 어떤 입력이 새 서버·모델로 전달되는지와 보관 정책을 먼저 정한다. 실제 사용자 자료를 무단 복제하지 않는다.
5. **소규모 명시적 사용:** `AGENT_V2_ENABLED`, 엔진 선택·트래픽 비율·MCP 도구 허용 목록 같은 서버 플래그로 제한한다. 브라우저만으로 권한 플래그를 켤 수 없게 한다. 기존 버튼·일정·저장 형식은 유지한다.
6. **점진적 확대:** 품질·제약 위반·오류·p95 지연·GPU 대기·비용·취소 완료율·인증 실패가 합의한 기준을 통과한 뒤 확대한다. 기준값은 부하·품질 측정 후 고정하며 현재 달성했다고 주장하지 않는다.
7. **롤백:** 신규 작업 접수 플래그를 끄고 기존 `/api/assistant`로 복귀한다. 진행 중 작업은 정책에 따라 완료 또는 취소하며 상태 조회는 보관 기간 동안 유지한다. DB 변경은 추가형으로 하고 기존 저장 초안을 손상시키지 않는다.

## 10. 구현 완료를 판정할 검사

### MCP 서비스를 실제로 구성하는 작업 단위

아래는 기존 앱 저장소에 추가할 **예정 구조**다. 현재 초기 MCP는 분석 저장소의 `services/jeju-mcp`에서 별도로 구현·검증했으며, 아래 전체 구조를 기존 앱에 생성·배포한 것은 아니다. 기존 `prototype/`의 배포 루트를 이동하지 않는다. 외부 서비스는 별도 빌드·배포 파이프라인을 둔다.

```text
jeju-tour/
  prototype/                    # 기존 Vercel 앱; 현재 빌드·라우팅 유지
  services/agent/
    gateway/                    # 작업 REST API·서비스 인증·권한
    worker/                     # 모델 공급자·LAG 어댑터·MCP client
    jobs/                       # 영속 상태·lease·취소·멱등성
  services/jeju-mcp/
    server/                     # POST /mcp, 요청별 server/transport
    tools/                      # 승인된 도구별 input/output schema
    adapters/                   # 장소·기상·경로·출처 공급자
    auth/                       # issuer/audience/scope/소유권 검증
    health/                     # liveness·도구별 readiness
  contracts/agent-v2/            # 기존 클라이언트와 새 서비스의 응답 계약
```

1. **의존성 고정:** 별도 서비스에 SDK 1.32.1과 호환 검증한 스키마 라이브러리·운영 Node 버전을 고정하고 잠금파일로 설치한다. 기존 앱 잠금파일을 무조건 최신 SDK로 올리지 않는다. 현재 SDK 요구는 Node 18 이상이지만 운영 LTS 버전은 실제 지원·보안 정책에 맞춰 고정한다.
2. **핸들러:** 요청 본문 크기·Origin·인증을 먼저 검사한다. 검증된 주체·scope로 요청별 서버와 stateless transport를 만들고 허용 도구만 등록한다. POST 처리를 SDK에 위임하고 요청 종료 시 관련 자원을 정리한다.
3. **도구:** `places.search`, `places.get`, `constraints.check`를 먼저 구현한다. 결과에 원천 ID·근거·갱신/미확인 상태를 포함한다. 웹 API 전체를 범용 HTTP 도구로 노출하지 않는다.
4. **외부 어댑터:** 공급자별 타임아웃·쿼터·캐시 키·응답 검증·오류 변환을 분리한다. ODsay는 원본 서버 확보 또는 허용된 별도 구현·접근 검증 후 활성화한다.
5. **서버 검증:** 실제 v1 MCP 클라이언트로 초기화→목록→정상 호출→잘못된 인자→권한 거부→공급자 실패를 확인한다. 단순 `curl` HTTP 200이나 열린 포트만으로 MCP 호환을 판정하지 않는다.
6. **배포:** 컨테이너/서비스에 health와 graceful shutdown을 구현한다. 새 요청을 막고 처리 중 요청의 제한 시간을 지킨 뒤 종료한다. Worker의 lease 회수와 서버 종료 정책을 함께 검증한다. 인증 키 교체·프로토콜 호환·모델/자료 버전별 복귀 절차를 남긴다.
7. **운영 관측:** 도구별 p50/p95·성공/부분/실패·권한 거부·쿼터 소진·근거 최신성·응답 크기를 기록한다. `liveness`는 프로세스 생존, `readiness`는 승인 도구의 필수 의존성이 준비됐는지로 구분하며 외부 공급자 하나의 장애를 전체 도구의 성공으로 감추지 않는다.

### 통합 인수 기준

| 검사 | 합격 조건 |
| --- | --- |
| 기존 Vercel 앱 연결 | 실제 대상 앱에서 설문/대화 → 신규 작업 → 결과 → 기존 코스 적용까지 확인. 독립 탐색기만 동작한 것으로 대체하지 않음 |
| MCP 계약 | 지원 버전 협상, 도구 목록·스키마, 성공/부분/실패 응답, 취소·재연결 테스트 통과 |
| 인증·소유권 | 만료·위조·잘못된 issuer/audience/scope·다른 사용자 job/plan 접근 거부; 정상 사용자만 조회·취소·저장 가능 |
| Origin·CSRF | Production·승인 Preview·서버 간 호출 정책을 각각 검증. 쿠키 사용 시 CSRF 부정 테스트 포함 |
| 제약·근거 | 존재하는 장소 ID뿐 아니라 필수 제약의 `unknown/fail/conflict`와 신선도·상충 근거 표시가 유지됨 |
| 작업 수명 | 워커/함수 재시작, 중복 전달, 새로고침, 타임아웃, SSE 단절, 취소 후 재조회에서도 상태가 일관됨 |
| 모델 호환 | Ollama 모델별 구조화 출력·한국어·취소·한도·자원 사용을 실제 실행해 확인하고 기존 기준선과 비교 |
| 롤백 | 신규 경로를 끈 뒤 기존 비서·설문·코스 저장이 유지되고 기존 작업 결과를 정책대로 회수 가능 |

계획 중 초기 읽기 전용 MCP 3개 도구는 구현·로컬 검증을 완료했다. 전체 서비스에는 논문 원문, 배포 상태 확인, 모델·GPU 선택, 외부 서버·인증·데이터 사용권 확인과 후속 개발이 이어져야 한다. 비밀값은 환경 설정의 보안 입력 경로로 제공하며 이 문서·채팅·코드에 기록하지 않는다.

## 11. MCP 버전·배포 출처

- [SDK 1.32.1에 대응하는 버전 상수](https://github.com/modelcontextprotocol/typescript-sdk/blob/ff07b001194fe60ee9deb2121cf119057565796d/src/types.ts#L4): `2025-11-25`와 지원 버전 목록.
- [v1 클라이언트 초기화](https://github.com/modelcontextprotocol/typescript-sdk/blob/ff07b001194fe60ee9deb2121cf119057565796d/src/client/index.ts#L464): 초기화·버전 협상·initialized 알림.
- [v1 stateless HTTP 예제](https://github.com/modelcontextprotocol/typescript-sdk/blob/ff07b001194fe60ee9deb2121cf119057565796d/src/examples/server/simpleStatelessStreamableHttp.ts#L105) 및 [transport 구현](https://github.com/modelcontextprotocol/typescript-sdk/blob/ff07b001194fe60ee9deb2121cf119057565796d/src/server/webStandardStreamableHttp.ts#L386).
- [2026-07-28 Streamable HTTP 규약](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/d4b317231161ee96d3eefb2448bda4230c76f4c6/docs/specification/2026-07-28/basic/transports/streamable-http.mdx), [SDK 버전별 차이](https://github.com/modelcontextprotocol/typescript-sdk/blob/919bb8ea88b6e10e72c2f8b6218fad4cfdc40b2c/docs/protocol-versions.md): 초기 v1 설계와 혼용하지 않을 변경사항.
- [공식 Vercel Labs MCP 어댑터](https://github.com/vercel/mcp-adapter/blob/57181555cbef0de65bdaa041db69f8434266ed66/README.md): 짧은 MCP HTTP 서비스를 Vercel에서 제공하는 선택지가 있으며, 조사 버전은 v2·Node 20 이상을 대상으로 함. 현재 앱에 그대로 설치한 상태가 아님.
- [MCP 토큰 보안 고려사항](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/d4b317231161ee96d3eefb2448bda4230c76f4c6/docs/specification/2026-07-28/basic/authorization/security-considerations.mdx): audience·원본 토큰 전달 방지 원칙. 실제 구현의 전체 인증 계약은 선택한 프로토콜 버전에 맞춰 검증.
