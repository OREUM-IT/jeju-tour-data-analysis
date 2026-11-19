# AWS Langflow 내부 연결과 기존 Vertex 자격증명 등록

2026-11-19 한국 시간. 사용자가 AWS `Ubuntu-1`의 실제 981곳 MCP·Langflow·전용 DB·Vertex 자격증명 등록에 이어 **`143b705` 적용 후 Vertex 단건 실응답 성공** 출력을 제공했다. Langflow는 41초에 Healthy가 됐고 `flowStatus:upgraded-vertex-client`, `vertexConfigured:true`다. 실제 요청은 HTTP 200·14.5초, 모델 호출 성공 4회·MCP 호출 성공 3회, 최종 검증 수락과 임시 실행 키 폐기를 확인했다. 후보 3곳은 미검증 실내 조건에 대해 `cannot_confirm`·`indoor_unverified`로 정상 안내했다. 단건 연결·검증 성공 범위이며 `productionReady:false`다. 다음은 **제한 Gateway 구현**이며 코드 구현·Access/Tunnel·Vercel 연결은 후속 단계다. AWS 결과는 사용자 제공 출력에 근거하며 작성자의 직접 원격 실행 결과가 아니다.

## 1. 추가되는 구성

- 공식 `langflowai/langflow:1.13.3` 이미지를 digest `sha256:0dea79f5a557e1602b5514973484332f479cdb6b71cceb098536ecc231b02d7d`로 고정한다. [공식 빌드 소스](https://github.com/langflow-ai/langflow/blob/v1.13.3/docker/build_and_push.Dockerfile)의 PostgreSQL 드라이버 포함 이미지다.
- Langflow는 UID 1000, 읽기 전용 루트 파일시스템, capability 제거, 3GiB 메모리·2 CPU 한도, 단일 worker로 실행한다. `/app/data` 전용 볼륨과 제한된 `/tmp`만 쓴다.
- 관리 포트는 **호스트 `127.0.0.1:7860`만** 연다. AWS 방화벽에 7860을 추가하지 않는다. 자동 로그인·공개 가입은 끈다.
- 별도 `langflow-db` PostgreSQL 17 인스턴스·볼륨을 사용한다. Langflow는 `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`인 전용 계정으로 자신의 DB에만 연결한다. 기존 관광용 bootstrap DB·계정·볼륨은 유지한다.
- Langflow는 MCP 내부망, 전용 DB 내부망, 외향 네트워크에만 연결한다. 내부 MCP 예외는 정확한 호스트명 `jeju-mcp`다. 외향 목적지 방화벽까지 구현한 구성은 아니다.
- 기존 로컬 v2 흐름 파일은 그대로 두고, 검증 컴포넌트 두 곳의 고정 주소와 MCP 등록을 AWS 내부 주소로 바꾼 사본을 가져온다. 최종 검증을 거치지 않는 출력 경로를 추가하지 않는다.

## 2. AWS SSH 터미널에서 실행

사용자가 이 절의 최초 기동·검사, 3절의 자격증명 등록과 4절의 실제 모델 단건 검사를 완료했다. 아래 명령은 재현용이며 현재 작업에서는 완료된 설치·등록·단건 요청을 반복하지 않는다.

[30 문서](30-lightsail-real-catalog.md)를 완료한 `~/oreumit-agent`, 기존 Compose 이름 `oreumit-bootstrap`을 기준으로 한다. `git status --short`에 수정 파일이 있으면 내용을 먼저 확인한다. 아래 블록은 오류가 발생하면 멈춘다. GitHub 로그인이나 원본 카탈로그 생성은 반복하지 않는다.

```bash
cd ~/oreumit-agent &&
git status --short &&
test -z "$(git status --porcelain)" &&
git fetch origin refs/heads/codex/lightsail-langflow:refs/remotes/origin/codex/lightsail-langflow &&
git switch --no-track -c codex/lightsail-langflow origin/codex/lightsail-langflow &&
sudo python3 deploy/prepare-langflow.py &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml config --quiet &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --build --no-deps --wait --wait-timeout 180 langflow-db &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --no-deps --wait --wait-timeout 300 langflow &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/check-langflow.sh
```

최초 브랜치 전환용 명령이다. 이미 `codex/lightsail-langflow`를 만들었다면 `git switch -c` 대신 `git switch codex/lightsail-langflow`를 사용한다. 원격 업데이트는 fetch 후 `git merge --ff-only origin/codex/lightsail-langflow`로 확인하며 강제 reset하지 않는다. PR #11은 main에, PR #12는 작업 브랜치에 병합됐다. Langflow 구성과 단건 실행기를 main에 반영하는 후속 변경은 [PR #13](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/13)이다.

첫 실행은 약 764MiB의 Langflow 이미지 다운로드·DB 마이그레이션 때문에 몇 분 걸릴 수 있다. 두 `up --no-deps` 명령은 새 DB와 Langflow만 시작한다. 이미 검증한 MCP와 기존 DB를 재생성하지 않는다.

사용자가 제공한 최초 성공 JSON은 다음과 같다. `langflow-db`·`langflow` Healthy와 SSRF·DB 권한 PASS도 함께 제공했다.

```json
{"status":"passed","langflowVersion":"1.13.3","authenticated":true,"dataMode":"app-export","placeCount":981,"toolCount":3,"flowStatus":"imported","vertexConfigured":false,"modelExecuted":false,"productionReady":false}
```

위 최초 출력의 `vertexConfigured:false`는 등록 전 상태다. 이후 3절을 완료한 사용자 출력에서는 `flowStatus:preserved-existing`, `vertexConfigured:true`로 확인됐다. 이 단계는 모델을 호출하지 않는다. 마지막 PASS와 고정 오류 코드만 공유하고 비밀 설정 파일은 공유하지 않는다.

## 3. 기존 Vertex AI 서비스 계정 JSON 등록

사용자가 이 절을 완료하고 다음 등록 결과를 제공했다. 후속 `check-langflow.sh`도 `flowStatus:preserved-existing`, `vertexConfigured:true`, `modelExecuted:false`, `productionReady:false`로 통과했다. 이 등록 출력만으로는 Google 공급자 인증 성공을 뜻하지 않는다. 이후 실제 요청 성공 근거는 4절에 별도로 기록하며, 아래 등록 명령은 재현용이다.

```json
{"status":"configured_without_inference","modelExecuted":false,"providerAccessVerified":false}
```

새 AI Studio 키는 필요 없다. [26 문서](26-langflow-vertex-auth.md)에서 사용한 **기존 Vertex AI 서비스 계정 JSON**을 재사용한다. JSON 원문은 채팅·Git·흐름 파일에 넣지 않는다. Google Cloud에서 이미 발급한 키는 나중에 다시 다운로드할 수 없으므로, 보관 중인 원본이나 승인된 비밀 저장소를 사용한다.

사용자는 원본이 Vercel 환경변수에만 있다고 확인했다. 연결된 Vercel 프로젝트 `jeju-tour`에서 Production 대상 `GOOGLE_SERVICE_ACCOUNT_JSON`의 존재와 `encrypted` 유형을 확인했다(값은 출력하지 않음). [프로젝트 환경변수 설정](https://vercel.com/seo-a91c/jeju-tour/settings/environment-variables)에서 이 변수의 기존 JSON 전체를 복사해 아래 AWS 편집기에 넣는다. 변수 이름이나 `GOOGLE_CLOUD_PROJECT` 값이 아니라 서비스 계정 JSON 객체가 필요하다. Vercel의 기존 값 수정·재배포는 이 복사 과정에 필요하지 않다. Secret 유형으로 값 조회가 제한된 경우에는 우회해 추출하지 않고 보관 원본 또는 승인된 별도 서비스 계정 발급 절차를 사용한다.

AWS에 해당 JSON 파일이 이미 있으면 그 경로를 사용한다. 직접 입력해야 하는 경우 아래처럼 서버의 비공개 디렉터리에서 편집할 수 있다. 기존 파일이 있으면 내용을 확인하지 않은 채 덮어쓰지 않는다.

```bash
sudo install -d -m 700 /etc/oreumit-agent
sudo nano /etc/oreumit-agent/vertex-service-account.json
sudo chmod 600 /etc/oreumit-agent/vertex-service-account.json
```

`nano`에서 기존 JSON 전체를 붙여넣고 `Ctrl+O` → Enter로 저장, `Ctrl+X`로 종료한다. 입력 값은 원본 서비스 계정 객체 또는 26 문서의 `serviceAccount`·`location: global` 구조다. 개인 키를 명령행 인자에 넣지 않는다.

그다음 **저장된 파일을 stdin으로만** 등록한다. 아래는 위 경로에 보관한 경우다.

```bash
cd ~/oreumit-agent &&
sudo sh -c 'docker compose -p oreumit-bootstrap -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow python /opt/oreumit/set-vertex.py < /etc/oreumit-agent/vertex-service-account.json' &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/check-langflow.sh
```

`configured_without_inference` 뒤 검사에서 `vertexConfigured:true`가 나오면 저장·참조까지 완료된 것이다. 기존 동일 이름 변수가 있으면 `existing_preserved`로 보존한다. 기존 값의 유효성이나 새 입력과 일치까지 보증하지 않는다. JSON 구조·RSA 개인 키 형식·Google 토큰 주소만 로컬 검증하고 **Google OAuth나 모델 API는 호출하지 않는다**. `providerAccessVerified:false`, `modelExecuted:false`가 정상이다. 이후 실제 모델 1건 검증 성공은 4절의 사용자 출력으로 별도 기록한다.

## 4. 실제 모델 요청 1건 검사

**사용자 제공 재검증 성공:** `143b705` 적용 후 Langflow가 41초에 Healthy가 됐고 흐름 업그레이드가 `upgraded-vertex-client`로 완료됐다. 이어 아래 실제 요청 한 건이 성공했다.

| 항목 | 사용자 제공 결과 |
|---|---|
| session ID | `aws-smoke-f26e43f5313b4e878a5510d3fa66662b` |
| 실행 완료 | `status:passed`, `phase:complete`, HTTP 200, 14.5초 |
| 모델·검증·키 정리 | `modelExecuted:true`, `finalValidationAccepted:true`, `temporaryApiKeyRevoked:true` |
| 최종 안내 | `action:cannot_confirm`, 후보 3곳, `confirmationNotice:true`, `noticeTopics:indoor_unverified` |
| 실행 기록 | trace 1건, 미완료 0건, 실패 0건 |
| 모델 호출 기록 | 관측 4회, 성공 4회 |
| MCP 호출 기록 | 성공 3회, 예상 밖 도구 호출 0회 |
| 운영 준비 | `productionReady:false` |

실내 조건을 확정할 검증 근거가 없으므로 `cannot_confirm`은 오류가 아니라 기대한 확인 필요 안내다. 이 결과는 실제 모델 연결·최종 검증·키 정리를 확인한 **단건 성공**이며, 추천 품질 전체·모든 제약 처리·부하 안정성을 검증한 것은 아니다. 아래 실패 경위와 적용 명령은 재현 기록이다. 성공한 실제 요청을 확인 목적으로 다시 반복하지 않는다.

최초 AWS 실행은 HTTP 500으로 2.7초 만에 실패했다. 사용자 출력에서 종료된 실패 trace 1개, 모델·도구 span 0개, 임시 API 키 삭제 성공을 확인했다. 공급자 호출 성공이나 검증된 추천으로 집계하지 않는다.

코드 검토와 별도의 오프라인 재현에서 초기화 차단 원인을 확인했다. Langflow 1.13.3의 공통 `get_llm()`은 `GOOGLE_API_KEY` 유무를 먼저 검사하고, 이후에야 Vertex 서비스 계정 override를 적용한다. AWS에 등록한 `JEJU_VERTEX_SERVICE_ACCOUNT_JSON`만으로는 이 공통 경로가 모델 클라이언트를 만들지 못한다. 실패 trace 자체가 내부 예외를 확정한 것은 아니며, 이 원인은 SDK 소스와 네트워크 없는 재현으로 확인했다.

`codex/lightsail-vertex-client` 수정은 AWS 사본의 `_get_llm()` 반환 부분만 서비스 계정을 받는 `ChatGoogleGenerativeAI(vertexai=True, google_api_key=None)`로 바꾼다. 기존 공급자 정책 검사와 지정 모델 `gemini-3.8-flash`, global 위치, SDK 60초·재시도 0회, Agent 90초·모델 최대 4회 제한을 유지한다. 기존 평가용 공개 흐름 파일은 변경하지 않는다. 수정 적용 후 AWS 재실행은 위 `143b705`의 사용자 제공 출력으로 성공을 확인했다.

기존 흐름에는 아래 명령으로 수정을 적용한다. `bootstrap.py`는 파일 단위 bind mount이므로 Git 갱신 뒤 Langflow 컨테이너만 재생성해 새 파일을 연결한다. 기존 환경변수·DB·볼륨을 그대로 사용한다. `--upgrade-vertex-client`는 전체 그래프가 알려진 구버전과 정확히 일치할 때만 `data`를 갱신하며, `If-Match`로 동시 편집을 거부한다. 수정된 흐름은 그대로 보존하고, 알 수 없는 사용자 변경은 덮어쓰지 않는다. Flow ID·설명·폴더·자격증명은 보존한다.

```bash
cd ~/oreumit-agent &&
git status --short &&
test -z "$(git status --porcelain)" &&
git fetch origin refs/heads/codex/lightsail-vertex-client:refs/remotes/origin/codex/lightsail-vertex-client &&
git merge --ff-only origin/codex/lightsail-vertex-client &&
sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --no-deps --force-recreate --wait --wait-timeout 300 langflow &&
sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow python /opt/oreumit/bootstrap.py --upgrade-vertex-client &&
sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow python - --run-once < deploy/langflow/run-once.py
```

검사에는 일회용 RSA 키·가상 서비스 계정으로 실제 SDK 클라이언트를 생성하는 단계가 있다. `GOOGLE_API_KEY`·`GEMINI_API_KEY`를 제거하고 소켓/DNS 연결을 차단하므로 Google OAuth·모델 호출 없이 초기화 경로를 확인한다. Docker CI에서는 비어 있는 테스트 DB에 구버전을 가져온 뒤, 일반 검사 거부 → 명시적 갱신 → 재실행 보존도 확인한다.

`deploy/langflow/run-once.py`는 기존 AWS 흐름과 자격증명을 검사한 뒤 합성 요청 **한 건**을 제출한다. 질문은 실내 방문 조건이 필요한 장소 후보 최대 3곳이며, 현재 미확인 근거를 고려한 기대 결과는 `cannot_confirm`과 `indoor_unverified` 안내다. 이 한 요청은 기존 Vertex API 사용료가 발생할 수 있고, 기존 Agent 제한에 따라 내부 모델 호출은 최대 4회다. 자동 재시도·모델 변경·한도 상향은 하지 않는다.

위 적용 블록은 마지막 줄에서 실제 요청까지 제출한다. 결과를 확인하기 전에 블록을 반복하지 않는다. 흐름 업그레이드를 완료한 뒤 실행기만 갱신하는 경우에는 실행기를 stdin으로 전달하므로 추가 컨테이너 재생성이 필요 없다. 로그인 JWT로 임시 Langflow 실행용 API 키(10분 만료)를 만들고, `x-api-key`로 한 요청을 보낸 뒤 정상·오류 경로 모두 삭제를 시도한다. 키와 원문 응답은 출력하지 않는다. 관리 API의 실행 결과에서 지정된 `ChatOutput-jejuV2`와 최종 검증 컴포넌트 출처만 선택하고, 동일한 원본 요청·카탈로그 버전으로 MCP에 한 번 더 검증한다. 같은 session/flow에 속한 PostgreSQL trace/span의 상태·종료 시각·종류·도구 이름만 읽어 성공한 모델 호출과 MCP 도구 호출을 확인한다. 프롬프트·원문 응답·오류·Credential DB 값은 조회하지 않는다.

성공 기준은 `status:passed`, `modelExecuted:true`, `finalValidationAccepted:true`, `temporaryApiKeyRevoked:true`와 완료된 실행 기록이다. `productionReady:false`는 유지한다. 모델명은 실행 설정이며 trace의 모델 표기를 Google이 서명한 응답 모델 식별자로 해석하지 않는다. `response_validated` 이후 실행 기록을 충분히 확인하지 못하면 `incomplete`로 보고한다. HTTP 시간 초과 뒤에는 원격 작업 중단이 보장되지 않으므로 출력의 sessionId·고정 오류 코드로 상태를 먼저 확인하고 명령을 반복하지 않는다. 키 삭제 실패는 `TEMPORARY_API_KEY_CLEANUP_FAILED`이며 해당 이름(sessionId)의 키를 관리자가 확인한다.

`--check-only`는 요청 준비, 임시 키 생성·삭제, 읽기 전용 DB 기록 조회만 검사하며 모델을 호출하지 않는다. 공식 Docker CI에서 이 모드의 준비·임시 키 생성/삭제·읽기 전용 DB 조회를 통과했다. CI에는 이 모드만 연결한다. 실제 AWS 모델 요청 성공은 이 절의 사용자 제공 단건 결과로 별도 기록하며 CI가 모델을 실행했다고 해석하지 않는다.

## 5. 저장·재시작·문제 확인

`prepare-langflow.py`는 새 관리자 비밀번호, DB 관리자·앱 비밀번호, Fernet 암호화 키를 각각 생성한다. `deploy/.local/langflow.env`와 `langflow-db.env`는 0600이며 기존 값은 그대로 보존한다. 둘 중 한 파일만 남은 불완전 상태는 자동 재생성하지 않는다. DB 데이터와 일치하는 백업으로 복구해야 한다.

Langflow의 Credential·API 키 등 비밀값 암호화와 DB 전체 암호화는 다르다. `LANGFLOW_SECRET_KEY`를 바꾸면 기존 저장 자격증명을 읽지 못할 수 있다. DB 볼륨만 남기고 비밀 파일을 지우지 않는다. 신규 Langflow DB 볼륨은 `oreumit-bootstrap_langflow_pgdata`, 설정·파일 볼륨은 `oreumit-bootstrap_langflow_data`이며 기존 `pgdata`와 분리된다.

```bash
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml ps
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml restart langflow
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --no-deps --wait --wait-timeout 300 langflow
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/check-langflow.sh
```

사용자 서버에서 `down --volumes`를 실행하지 않는다. 초기 단계로 돌아가야 하면 새 서비스 `langflow langflow-db`만 `stop`하고 파일·볼륨은 보존한다. 전체 Compose 출력, 환경변수가 포함된 inspect, 인증 파일, 원문 DB dump를 채팅에 보내지 않는다. 관리 UI가 필요할 때는 운영자 PC의 인증된 SSH 포워딩을 별도로 설정한다. AWS 브라우저 SSH 탭은 PC의 `localhost:7860` 포워딩을 자동으로 만들지 않는다.

## 6. 검증 범위와 다음 단계

배포 코드의 단위 검사는 비밀 파일 보존·부분 실패·링크 거부, 인증 거부·MCP 등록 충돌·흐름 변조 거부, Vertex JSON·RSA·고정 OAuth 주소를 다룬다. 공식 이미지와 전용 DB의 [Docker CI 기동 검사](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44477702783)에서 로그인·SSRF 허용/거부·MCP 도구 발견·흐름 가져오기·재시작 보존을 통과했다. 초기 기동에서 로그 상위 디렉터리가 아직 없던 오류는 기존 볼륨 루트의 `/app/data/langflow.log`로 경로를 지정해 해결했다. **CI 카탈로그의 981곳은 모두 명시적인 허구 테스트 자료**이며 실제 제주 자료 검증이나 모델 성능 측정이 아니다. 실제 AWS 카탈로그 연결 성공은 30 문서의 사용자 PASS 출력, AWS Langflow 내부 기동·인증·SSRF·MCP·흐름 가져오기·DB 권한 성공은 이 문서 2절의 사용자 출력으로 별도 기록한다. Vertex 자격증명 등록과 기존 흐름 보존도 3절의 사용자 출력으로 확인했다. 수정 적용 후 실제 Vertex 모델 요청·최종 검증 수락·임시 키 폐기는 4절의 사용자 제공 단건 결과로 확인했다. AWS DB 재시작·백업 복원·운영 안정성까지 검증한 것은 아니다.

작성자의 로컬 Docker는 32GB 저장 공간과 VFS 드라이버 한도 때문에 공식 이미지의 실행 복사 단계에서 실패했다. 공식 이미지의 CI 실행 성공과 사용자가 제공한 AWS 기동 성공 출력은 별도의 근거로 기록한다. AWS의 150GB 여유 공간 부족을 관측한 것은 아니다.

다음 작업은 **제한 Gateway 구현**다. Gateway 코드 구현과 Access·Tunnel·Vercel 연결은 후속 단계이며 아직 완료하지 않았다. 기존 Vertex 자격증명 등록과 실제 단건 응답·최종 검증은 사용자 제공 출력으로 완료됐으므로 같은 성공을 확인하기 위한 반복 실행은 필요하지 않다. [PR #14](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/14)는 CI 통과 후 `main@4579c25`에 병합됐다. 기존 평가의 문맥 조건·호출 실패 한계와 [22 보안 평가](22-jailbreak-and-prompt-injection-defense.md), 백업 복원·부하·운영 취소 검증은 남아 있다. 이번 내부 연결·실응답 단건 성공을 운영 추천 서비스 전체 완료로 표시하지 않는다.

## 7. 다음 Gateway 구현의 계약과 검증 기준

웹 작업 브랜치 `codex/document-questionnaire@3636516`와 현재 분석 저장소를 읽어 확인한 준비 사항이다. 현재 웹은 `ThemeQuiz`의 규칙 기반 테마 배정과 `/api/course/places`를 사용하며 Gateway 서버 어댑터·요청 경로·환경변수는 아직 없다. 아래는 다음 구현의 기준이며 Gateway가 배포됐다는 의미가 아니다.

- **설문 조건 보존:** 웹의 동행·교통·걷기·음식·날짜 범위·자유 입력과 Agent의 검증 계약을 명시적으로 변환한다. 현재 `recommendation-validation.mjs`의 필수 조건은 실내·휠체어·방문 시각·예산 4종이다. 지원하지 않는 필수 조건은 버리거나 충족으로 표시하지 않고 추가 확인 대상으로 남긴다.
- **고정 실행 경로:** 서버가 승인된 flow ID·모델·MCP 주소·출력 컴포넌트를 선택한다. 요청자가 `tweaks`, 코드, 내부 URL이나 비밀값을 지정하지 못하게 한다. Langflow API 키에는 flow별 scope가 없으므로 Gateway에서 경로를 제한하고 전용 실행 키를 사용한다. 관리자 비밀번호·DB 자격·Vertex JSON을 Gateway에 전달하지 않는다.
- **인증·중복 실행:** Vercel에서 확인한 요청 주체를 서버 간 인증 문맥에 결합한다. nonce 만료·재사용 차단·중복 실행 방지를 구현한다. 현재 UUID request/session ID는 응답 결합용이며 재전송 차단 기능이 아니다. 공개 연결 전에는 Access 인증과 origin 검증을 추가한다.
- **검증된 결과만 반환:** `run-once.py`의 고정 Langflow 요청, `ChatOutput-jejuV2` 선택, `JejuFinalValidation-v2` 출처와 session 검사를 분리 재사용한다. MCP `/prepare-request`와 `/validate-response`로 원본 요청·카탈로그·정책을 대조하고 수락된 구조화 결과만 웹으로 보낸다. 단건 실행기의 관리자 로그인·임시 키 발급·DB 조회 전체를 웹 서버 경로로 옮기지 않는다.
- **시간·사용량·실패:** 본문·출력 크기, 동시성, 사용자별 한도, 전체 deadline과 도구 예산을 제한한다. Agent 90초·모델 4회 제한은 MCP 총호출 상한이 아니다. 현재 Vercel 함수의 120초보다 짧은 전체 처리 예산을 검증하며 timeout·차단·잘못된 응답은 고정 오류로 반환한다. 자동 모델 재시도를 추가하지 않는다.
- **도구 실행 정책:** MCP의 세 도구 schema 검사를 유지하고, 실제 호출마다 원본 필수 조건·사용자 범위·남은 예산을 확인하는 경로를 추가한다. 현재 `agent-tools.mjs`에는 이 요청별 정책이 없다. 공개 연결 전에 해당 정책과 목적지·리다이렉트 거부를 검증한다.

첫 구현은 비공개 네트워크의 단일 추천 경로와 별도 인증, 고정 upstream, 최종 검증, 모의 Langflow/MCP 오류 검사까지다. readiness는 모델을 호출하지 않는다. `examples/ollama-tunnel/gateway.mjs`는 Ollama용이며 그대로 Langflow에 연결하지 않는다. 이후 내부 검증 → Access origin 검증 → Gateway만 연결하는 Tunnel → Vercel Preview 순서로 진행한다. 공개 호스트명·Access 조직 주소/AUD와 서비스 비밀값은 실제 연결 단계에서 설정한다.
