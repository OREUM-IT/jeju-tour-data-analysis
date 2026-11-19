# AWS Langflow 내부 연결과 기존 Vertex 자격증명 등록

2026-11-19 한국 시간. 사용자가 AWS `Ubuntu-1`에서 **실제 981곳·MCP 세 도구·인증 거부·카탈로그 SHA256 일치** 검사를 통과한 출력을 제공했다. 서버는 x86_64, 메모리 7.6GiB, 루트 디스크 여유 약 150GB다. 이번 단계는 그 서버에 Langflow를 추가할 파일과 실행 절차이며, 작성자가 AWS에 직접 접속해서 배포했다는 뜻은 아니다.

## 1. 추가되는 구성

- 공식 `langflowai/langflow:1.13.3` 이미지를 digest `sha256:0dea79f5a557e1602b5514973484332f479cdb6b71cceb098536ecc231b02d7d`로 고정한다. [공식 빌드 소스](https://github.com/langflow-ai/langflow/blob/v1.13.3/docker/build_and_push.Dockerfile)의 PostgreSQL 드라이버 포함 이미지다.
- Langflow는 UID 1000, 읽기 전용 루트 파일시스템, capability 제거, 3GiB 메모리·2 CPU 한도, 단일 worker로 실행한다. `/app/data` 전용 볼륨과 제한된 `/tmp`만 쓴다.
- 관리 포트는 **호스트 `127.0.0.1:7860`만** 연다. AWS 방화벽에 7860을 추가하지 않는다. 자동 로그인·공개 가입은 끈다.
- 별도 `langflow-db` PostgreSQL 17 인스턴스·볼륨을 사용한다. Langflow는 `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`인 전용 계정으로 자신의 DB에만 연결한다. 기존 관광용 bootstrap DB·계정·볼륨은 유지한다.
- Langflow는 MCP 내부망, 전용 DB 내부망, 외향 네트워크에만 연결한다. 내부 MCP 예외는 정확한 호스트명 `jeju-mcp`다. 외향 목적지 방화벽까지 구현한 구성은 아니다.
- 기존 로컬 v2 흐름 파일은 그대로 두고, 검증 컴포넌트 두 곳의 고정 주소와 MCP 등록을 AWS 내부 주소로 바꾼 사본을 가져온다. 최종 검증을 거치지 않는 출력 경로를 추가하지 않는다.

## 2. AWS SSH 터미널에서 실행

[30 문서](30-lightsail-real-catalog.md)를 완료한 `~/oreumit-agent`, 기존 Compose 이름 `oreumit-bootstrap`을 기준으로 한다. `git status --short`에 수정 파일이 있으면 내용을 먼저 확인한다. 아래 블록은 오류가 발생하면 멈춘다. GitHub 로그인이나 원본 카탈로그 생성은 반복하지 않는다.

```bash
cd ~/oreumit-agent &&
git status --short &&
git fetch origin refs/heads/codex/lightsail-langflow:refs/remotes/origin/codex/lightsail-langflow &&
git switch --no-track -c codex/lightsail-langflow origin/codex/lightsail-langflow &&
sudo python3 deploy/prepare-langflow.py &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml config --quiet &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --build --no-deps --wait --wait-timeout 180 langflow-db &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --no-deps --wait --wait-timeout 300 langflow &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/check-langflow.sh
```

최초 브랜치 전환용 명령이다. 이미 `codex/lightsail-langflow`를 만들었다면 `git switch -c` 대신 `git switch codex/lightsail-langflow`를 사용한다. 원격 업데이트는 fetch 후 `git merge --ff-only origin/codex/lightsail-langflow`로 확인하며 강제 reset하지 않는다. 이 브랜치는 PR #11 위의 후속 변경이며 main에 자동 병합하지 않는다.

첫 실행은 약 764MiB의 Langflow 이미지 다운로드·DB 마이그레이션 때문에 몇 분 걸릴 수 있다. 두 `up --no-deps` 명령은 새 DB와 Langflow만 시작한다. 이미 검증한 MCP와 기존 DB를 재생성하지 않는다.

성공 시 JSON에 다음이 나온다.

```json
{"status":"passed","langflowVersion":"1.13.3","authenticated":true,"dataMode":"app-export","placeCount":981,"toolCount":3,"flowStatus":"imported","vertexConfigured":false,"modelExecuted":false,"productionReady":false}
```

재실행의 `flowStatus`는 `preserved-existing`이다. `vertexConfigured:false`는 다음 절의 자격증명을 아직 넣지 않았다는 뜻이다. 이 단계는 모델을 호출하지 않는다. 마지막 PASS와 고정 오류 코드만 공유하고 비밀 설정 파일은 공유하지 않는다.

## 3. 기존 Vertex AI 서비스 계정 JSON 등록

새 AI Studio 키는 필요 없다. [26 문서](26-langflow-vertex-auth.md)에서 사용한 **기존 Vertex AI 서비스 계정 JSON**을 재사용한다. JSON 원문은 채팅·Git·흐름 파일에 넣지 않는다. Google Cloud에서 이미 발급한 키는 나중에 다시 다운로드할 수 없으므로, 보관 중인 원본이나 승인된 비밀 저장소를 사용한다.

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

`configured_without_inference` 뒤 검사에서 `vertexConfigured:true`가 나오면 저장·참조까지 완료된 것이다. 기존 동일 이름 변수가 있으면 `existing_preserved`로 보존한다. 기존 값의 유효성이나 새 입력과 일치까지 보증하지 않는다. JSON 구조·RSA 개인 키 형식·Google 토큰 주소만 로컬 검증하고 **Google OAuth나 모델 API는 호출하지 않는다**. `providerAccessVerified:false`, `modelExecuted:false`가 정상이다. 실제 모델 1건 검증은 다음 단계에서 별도로 수행한다.

## 4. 저장·재시작·문제 확인

`prepare-langflow.py`는 새 관리자 비밀번호, DB 관리자·앱 비밀번호, Fernet 암호화 키를 각각 생성한다. `deploy/.local/langflow.env`와 `langflow-db.env`는 0600이며 기존 값은 그대로 보존한다. 둘 중 한 파일만 남은 불완전 상태는 자동 재생성하지 않는다. DB 데이터와 일치하는 백업으로 복구해야 한다.

Langflow의 Credential·API 키 등 비밀값 암호화와 DB 전체 암호화는 다르다. `LANGFLOW_SECRET_KEY`를 바꾸면 기존 저장 자격증명을 읽지 못할 수 있다. DB 볼륨만 남기고 비밀 파일을 지우지 않는다. 신규 Langflow DB 볼륨은 `oreumit-bootstrap_langflow_pgdata`, 설정·파일 볼륨은 `oreumit-bootstrap_langflow_data`이며 기존 `pgdata`와 분리된다.

```bash
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml ps
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml restart langflow
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml up -d --no-deps --wait --wait-timeout 300 langflow
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/check-langflow.sh
```

사용자 서버에서 `down --volumes`를 실행하지 않는다. 초기 단계로 돌아가야 하면 새 서비스 `langflow langflow-db`만 `stop`하고 파일·볼륨은 보존한다. 전체 Compose 출력, 환경변수가 포함된 inspect, 인증 파일, 원문 DB dump를 채팅에 보내지 않는다. 관리 UI가 필요할 때는 운영자 PC의 인증된 SSH 포워딩을 별도로 설정한다. AWS 브라우저 SSH 탭은 PC의 `localhost:7860` 포워딩을 자동으로 만들지 않는다.

## 5. 검증 범위와 다음 단계

배포 코드의 단위 검사는 비밀 파일 보존·부분 실패·링크 거부, 인증 거부·MCP 등록 충돌·흐름 변조 거부, Vertex JSON·RSA·고정 OAuth 주소를 다룬다. Docker CI는 공식 이미지와 전용 DB에서 로그인·SSRF 허용/거부·MCP 도구 발견·흐름 가져오기·재시작 보존을 확인하도록 구성했다. **CI 카탈로그의 981곳은 모두 명시적인 허구 테스트 자료**이며 실제 제주 자료 검증이나 모델 성능 측정이 아니다. 실제 AWS 카탈로그 연결 성공은 사용자가 제공한 30 문서의 PASS 출력으로 별도 기록한다.

작성자의 로컬 Docker는 32GB 저장 공간과 VFS 드라이버 한도 때문에 공식 이미지의 실행 복사 단계에서 실패했다. 공식 이미지 실행 결과는 CI에서 확인하며, 이를 AWS 실행 성공으로 바꾸어 기록하지 않는다. AWS의 150GB 여유 공간 부족을 관측한 것은 아니다.

남은 순서는 **Vertex OAuth·모델 요청 1건 확인 → 추천 최종 검증과 실패 처리 → 제한 Gateway → Access·Tunnel → Vercel Preview**다. 기존 평가의 문맥 조건·호출 실패 한계와 [22 보안 평가](22-jailbreak-and-prompt-injection-defense.md), 백업 복원·부하·운영 취소 검증은 남아 있다. 이번 내부 기동을 운영 추천 서비스 전체 완료로 표시하지 않는다.
