# 제주 관광 읽기 전용 MCP 서버 — 로컬 통합 단계

실행 가능한 HTTP MCP 서버다. **운영 배포·Vercel 앱 연결·LLM Agent·추천 품질 검증이 완료된 서비스는 아니다.** 기본 데이터는 가상의 fixture이며, 기존 프로젝트의 장소 목록을 검증해 가져오는 명령을 별도로 제공한다.

구현 범위:

- SDK `1.32.1`의 stateless Streamable HTTP. 요청별 서버/transport 생성, 실제 클라이언트 초기화·도구 발견·호출.
- `places.search`: 문자열·분류 검색. 기존 장소 ID 유지. 개인화 순위 추천 알고리즘은 아님.
- `places.get`: 최대 20개 ID의 상세·누락 ID 반환.
- `constraints.check`: 휠체어 접근·실내 여부·방문 비용의 명시 조건 검사. 값과 해당 필드의 유효한 근거가 있을 때만 pass/fail, 없거나 만료면 unknown.
- 서비스 Bearer 인증, 정확한 Origin 허용 목록, 입력 크기·메서드 제한. 사용자 OAuth·개인정보 도구·저장 도구는 없음.

## 1. 설치와 모의 검사

현재 지원되는 Node.js LTS를 사용한다. 최소 Node 22이며 현재 클라우드에서는 Node 24로 확인한다.

```sh
cd services/jeju-mcp
npm ci --ignore-scripts --no-audit --no-fund
npm test
```

캐시 경로가 쓰기 불가능한 클라우드에서는 `--cache /workspace/scratch/npm-cache`를 npm 명령에 추가한다. 모델·GPU·외부 API 계정은 이 검사에 필요 없다. 잠금파일을 유지하며 SDK v2 규격을 일부 혼합하지 않는다.

## 2. 가상 데이터로 실행

```sh
node scripts/configure-local.mjs
node --env-file=.env.local src/server.mjs
```

설정 명령은 `.env.local`에 임의 서비스 토큰을 생성하며 값을 출력하거나 기존 파일을 덮어쓰지 않는다. Windows에서도 해당 파일을 본인 계정의 보호된 폴더에 보관한다. 이 파일을 Git·채팅·공유 ZIP에 포함하지 않는다.

기본 주소는 `127.0.0.1:8788`이다. 별도 터미널에서 실행한다.

```sh
node --env-file=.env.local scripts/check-client.mjs
```

실제 SDK 클라이언트가 초기화 → 도구 목록 → 검색 → 상세 → 조건 검사를 수행한다. `dataMode:fixture`는 가상 데이터 검사임을 뜻한다. 이때 관광객에게 실제 장소 추천을 제공해서는 안 된다.

## 3. 기존 장소 자료 가져오기

원본 웹 저장소의 `prototype/data/jeju_tour_data.json`만 지원하는 명시적 변환기다. 원본의 Git remote·HEAD·추적 파일 내용이 일치하는지 확인하며 원본 파일을 변경하지 않는다.

현재 클라우드의 예시 명령(분석 저장소 루트에서 실행):

```sh
node services/jeju-mcp/scripts/import-catalog.mjs --input /workspace/scratch/reference-repos/jeju-tour/prototype/data/jeju_tour_data.json --output services/jeju-mcp/.local/catalog.json
```

출력은 `.local/*.json`이어야 하며 기존 파일을 덮어쓰지 않는다. 기존 ID·이름·분류·좌표를 보존하고 중복/잘못된 좌표는 명시적 진단과 함께 제외한다. 접근성·실내·가격을 분류나 설명에서 추측하지 않는다. 출처 기록은 데이터 출처이며, 접근성이나 영업 조건의 증거가 아니다.

현재 출처 `b0602a175c1d08959b7187da2b29ac450b52e0ba`에서 7,561행을 가져올 수 있다. 이는 운영 가능한 POI 수·최신 정보·데이터 사용권의 검증 완료를 뜻하지 않는다. 새 자료를 반영할 때 버전과 제외 기록을 검토한다.

새 설정 폴더 또는 아직 `.env.local`이 없는 서비스 폴더에서는 다음처럼 선택할 수 있다.

```sh
cd services/jeju-mcp
node scripts/configure-local.mjs --catalog .local/catalog.json
node --env-file=.env.local src/server.mjs
```

위 `cd`는 가져오기 명령을 실행한 저장소 루트 기준이다. 설정 생성기는 큰따옴표·줄바꿈이 포함된 카탈로그 경로를 거부한다. 기존 `.env.local`이 있으면 토큰을 재생성하지 말고 운영자만 `MCP_CATALOG_PATH`를 로컬 카탈로그의 경로로 수정한 뒤 서버를 재시작한다. 도구 호출로 카탈로그 경로나 파일을 선택할 수 없다. 외부 URL을 카탈로그로 받지 않는다.

## 4. 설정과 엔드포인트

| 설정 | 용도 |
|---|---|
| `MCP_SERVICE_TOKEN` | 32자 이상 서버용 토큰. Cloudflare 토큰과 별개 |
| `MCP_CATALOG_PATH` | 운영자가 지정한 로컬 JSON 경로. 생략 시 가상 fixture |
| `MCP_ALLOWED_ORIGINS` | 쉼표로 구분한 정확한 Origin. Origin이 있으면 일치해야 하며 없는 서버 호출도 서비스 인증 필수 |
| `HOST`, `PORT` | 기본 `127.0.0.1`, `8788`. 같은 호스트의 cloudflared에서 연결 |
| `MCP_SERVER_URL` | 확인 클라이언트용 전체 주소. 기본 `http://127.0.0.1:8788/mcp` |
| `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | 고정 Tunnel을 Access로 보호한 경우 확인 클라이언트에서 함께 사용 |

`GET /healthz`는 프로세스 생존만, 인증된 `GET /readyz`는 카탈로그 모드·건수·읽기 전용 상태를 반환한다. `productionReady:false`는 실제 자료를 가져와도 유지한다. 권한·데이터 품질·운영 통합 검증이 자동 완료되는 것은 아니기 때문이다.

`POST /mcp`는 JSON MCP 요청이다. GET/DELETE는 이 초기 stateless 구현에서 405를 반환한다. 브라우저에 토큰을 넣거나 CORS `*`로 직접 연결하지 않는다. Cloudflare Access가 추가되어도 서비스 토큰 검사는 유지된다.

카탈로그 결과는 외부 데이터다. 향후 LLM 연결에서는 결과의 설명문을 지시로 실행하지 않고, 출력 장소 ID·제약·근거를 별도 검증해야 한다. 현재 도구는 셸·SQL·원격 URL 호출·코스 저장·모델 호출을 수행하지 않는다.

## 5. Agent 카탈로그와 평가셋 (로컬 전용 산출물)

MCP 도구와 별개인 오프라인 스크립트다. 서버를 띄우지 않고, 모델을 호출하지 않는다. 출력에는 제3자 장소 자료가 들어 있어 `.local`에만 쓰며 기존 파일을 덮어쓰지 않는다.

- Agent 카탈로그 v2(근거 등급): `scripts/dump-app-places.mjs` → `scripts/export-agent-catalog.mjs`. 절차는 [19 문서](../../docs/19-agent-step2-data-foundation.md) 5절.
- 3단계 평가셋: `scripts/build-evaluation-set.mjs`. 카탈로그 v2, 페르소나, 테마 매핑, [평가 명세](../../data/evaluation-spec.json)로 96문항의 정답을 규칙으로 계산한다. `--report`는 이름·ID 없는 집계만 쓰고, `--check`는 다시 만든 결과가 파일과 같은지 비교한다. `--queries`로 넣는 작성 질의는 문장 규칙(200자 이하, 명시한 조건을 모두 언급, 명시하지 않은 조건을 가리키는 목록의 낱말·숫자와 후보 장소 이름 금지)을 통과해야 붙는다. 낱말 목록은 사실 요약 파일에 함께 실린다. 절차와 규칙은 [23 문서](../../docs/23-agent-step3-evaluation-set.md).
- 채점: `src/evaluation-scoring.mjs`의 `createScorer`가 구조화 응답을 문항과 대조한다(허용되지 않은 ID, 필수 조건 오판, 재현율·정밀도@5, 고지, 동작). 자유 문장은 읽지 않는다.
- 기준선 하네스: `npm run evaluate:baseline -- --help`로 `prepare`·`import`·`score`·`self-check` 명령을 확인한다. Gemini·Langflow 응답을 가져와 같은 평가셋으로 비교하며, 자유 문장은 원문 해시에 연결한 사람 판정을 요구한다. 문항 누락·중복·해시 불일치를 거부하고 모든 실패를 분모에 남긴다. 실제 모델 호출은 하지 않는다. 명령과 캡처 형식은 [24 문서](../../docs/24-agent-baseline-scoring.md)를 따른다.

`npm test`는 이 스크립트들을 합성 값으로 검사한다(2026-11-19: 95개 통과). 실제 카탈로그나 평가셋 파일은 테스트에 필요 없다.

## 6. oreumit.com의 다음 연결

1. 사용자가 저장한 Cloudflare nameserver의 전파와 `Active`, 기존 웹 HTTPS를 확인한다.
2. 이 서버를 상시 실행할 호스트를 정한다. 현재 Codex 작업공간은 개발·검증 환경이며 24시간 운영 호스트로 배포 완료된 상태가 아니다.
3. Access 정책을 먼저 구성한 뒤 `mcp.oreumit.com`을 같은 호스트의 `http://127.0.0.1:8788`로 연결한다. 클라이언트 주소는 `https://mcp.oreumit.com/mcp`다.
4. 서버 전용 비밀 설정으로 서비스 인증을 전달하며 실제 SDK 클라이언트로 재검증한다. `check-client.mjs`는 두 Cloudflare 서비스 토큰 필드가 모두 설정되면 해당 헤더도 전송한다.
5. Vercel Preview의 서버 코드에서 짧은 조회부터 연결하고 기존 로그인·검색·코스 저장을 확인한다. 아직 Vercel 서버 어댑터를 수정하거나 배포하지 않았다.
6. 장시간 LAG·모델 비교·Ollama 추론은 후속 Worker와 영속 작업으로 연결한다. DB·개인별 OAuth·도구별 권한·사용량 제한·운영 관측은 그 단계의 별도 구현 항목이다.

Hugging Face 계정 연결과 자동 검사 절차는 [조직·MCP·CI 운영 설정](../../docs/16-huggingface-organization-and-ci.md)을 따른다. 이 서비스의 로컬 검사에는 Hugging Face 토큰이 필요 없다.
