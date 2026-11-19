# Lightsail Docker 설치 다음 단계: 비공개 MCP·DB 기동

기준일: 2026-11-19 한국 시간. 기준 소스는 PR #10이 병합된 `main@b1be980`이다. 사용자는 Lightsail 인스턴스 `Running`, Docker hello-world 성공, Compose `v5.8.0` 출력을 보고했다. 이 문서는 그 다음에 실행할 파일과 절차다. AWS 계정·SSH에 직접 접속해서 확인하거나 배포한 결과는 아니다.

## 1. 이번 단계의 범위

- [Compose](../deploy/compose.yaml)는 **가상 데이터 MCP와 비어 있는 PostgreSQL·pgvector DB**만 시작한다. MCP는 기존 JSON fixture를 읽으며 DB와 아직 연결하지 않는다.
- 현재 v2 코드와 실제 모델 평가 결과는 그대로 보존한다. 이 fixture 기동 검사는 981곳 카탈로그·v2 흐름·추천 품질·보안 평가의 재실행이 아니다.
- Langflow·모델·Gateway·cloudflared·Vercel 연결은 포함하지 않는다. 모델 키와 유료 API 호출이 필요 없다. 이미 생성한 AWS 서버 비용은 별개다.
- MCP는 컨테이너 안에서 `0.0.0.0:8788`, 호스트에서는 **`127.0.0.1:8788`만** 공개한다. DB는 호스트 포트를 만들지 않는다. 두 서비스의 Docker 네트워크는 서로 분리된 `internal` 네트워크다.
- MCP는 비특권 `node` 사용자·읽기 전용 파일시스템·capability 제거로 실행한다. PostgreSQL은 공식 entrypoint의 초기 볼륨 권한 설정 뒤 DB 프로세스를 postgres 사용자로 실행한다. 모든 단계가 non-root라고 주장하지 않는다.
- 메모리·CPU·PID·로그 한도와 DB 영속 볼륨을 둔다. 이는 부하 시험·백업 완료를 뜻하지 않는다. Docker 소켓이나 호스트 홈을 마운트하지 않는다.

## 2. AWS SSH 터미널에서 실행

아래는 **처음 내려받는 경우**다. 명령을 한 줄씩 실행하고 오류가 나면 다음 명령을 진행하지 않는다. 같은 이름의 폴더가 있으면 삭제하거나 덮어쓰지 말고 `git status`와 브랜치를 확인한다. 이 브랜치는 검토용이며 main에 자동 병합하지 않는다.

```bash
sudo apt-get install -y git python3
git clone --branch codex/lightsail-docker-bootstrap --single-branch https://github.com/OREUM-IT/jeju-tour-data-analysis.git oreumit-agent
cd oreumit-agent
python3 deploy/prepare.py
sudo docker compose -f deploy/compose.yaml up -d --build --wait --wait-timeout 120
sudo bash deploy/check.sh
```

[prepare.py](../deploy/prepare.py)는 `deploy/.local/`을 0700, `mcp.env`와 `db.env`를 0600으로 만들고 각각 독립된 난수 토큰·비밀번호를 생성한다. 값을 출력하지 않으며 다시 실행해도 기존 값을 덮어쓰지 않는다. 해당 경로는 Git에서 제외된다. 서버 간 파일 복사나 채팅 전달로 키를 공유할 필요가 없다.

환경변수 비밀값은 Docker 관리자에게 보일 수 있다. `docker compose config`의 전체 출력이나 `docker inspect`의 환경변수 출력을 공유하지 않는다. 설정 검사만 할 때는 `docker compose -f deploy/compose.yaml config --quiet`를 쓴다. 이 초기 파일 방식은 운영 비밀 저장소·키 회전 체계를 대신하지 않는다.

성공 기준:

1. `up --wait`가 성공하고 두 컨테이너가 healthy다.
2. [check.sh](../deploy/check.sh)의 MCP SDK 초기화·3도구 검사에서 `status:passed`, `dataMode:fixture`, `productionReady:false`가 나온다.
3. 토큰 없는 요청은 401로 거부되고 인증된 readiness만 성공한다.
4. DB에 비밀번호로 TCP 접속해 pgvector 확장 존재와 벡터 거리 연산을 확인한다.

`fixture`는 허구 장소다. 이를 실제 관광객에게 추천하거나 `productionReady`를 true로 바꾸지 않는다. DB에는 아직 관광 데이터·임베딩·회원 정보가 없다.

## 3. 중지·재시작·문제 확인

```bash
sudo docker compose -f deploy/compose.yaml ps
sudo docker compose -f deploy/compose.yaml logs --tail 50 jeju-mcp
sudo docker compose -f deploy/compose.yaml stop
sudo docker compose -f deploy/compose.yaml up -d --wait --wait-timeout 120
```

- 8788이 사용 중이면 기존 서비스를 종료하지 말고 충돌을 먼저 확인한다.
- 초기 다운로드가 실패하면 오류를 확인한 뒤 같은 `up` 명령을 다시 실행한다. SSL 검증을 끄지 않는다.
- DB 초기 SQL은 **빈 볼륨에서 처음 실행할 때만** 적용된다. 초기화 오류가 나면 데이터를 지우지 말고 로그를 확인한다.
- `pg_isready`는 접속 준비만 뜻한다. pgvector 확인은 반드시 `check.sh` 결과로 판단한다.
- DB 비밀번호 파일만 바꿔도 기존 DB의 비밀번호가 자동 변경되지는 않는다. 기존 파일·볼륨은 보존한다.
- 일반 중지는 `stop`을 사용한다. `down`도 기본적으로 명명 볼륨은 남기지만 **`down --volumes`는 DB를 삭제**하므로 사용자 서버에서 사용하지 않는다.
- DB 백업·복구는 아직 마련하지 않았다. 개인 데이터와 실제 여행 기록을 넣기 전에 별도 백업·복원 절차와 최소 권한 DB 계정을 만든다. `bootstrap_admin`은 초기 관리자이며 향후 Agent/Langflow가 공유할 계정이 아니다.

## 4. 검증 환경과 제한

Node `24.22.1-bookworm-slim`과 upstream `pgvector/pgvector:pg17` 이미지를 실제 레지스트리에서 내려받아 Dockerfile에 digest를 고정했다. 이미지 취약점 검사·전체 공급망 검증을 마쳤다는 뜻은 아니다. PostgreSQL 버전과 확장 버전은 `check.sh` 출력으로 확인한다.

검증용 Codex 환경에서는 npm HTTPS에 세션 CA가 필요했다. 원본 Dockerfile과 같은 소스·잠금파일을 사용하는 임시 Dockerfile의 npm 단계에만 BuildKit secret으로 CA를 마운트했다. TLS 검증은 유지했고 이미지에 CA·프록시 자격을 복사하지 않았다. 사용자 AWS 명령과 GitHub CI는 원본 Dockerfile을 사용한다. 사용자 서버의 성공 여부는 위 실제 출력으로 별도 확인해야 한다.

검증 환경에서 MCP SDK 초기화·도구 3개 호출, 토큰 없는 readiness 401, 인증된 fixture/readOnly/productionReady=false, PostgreSQL 17.12·pgvector 0.8.7의 거리 연산을 확인했다. Docker inspect로 MCP 호스트 바인딩 `127.0.0.1:8788`, DB 공개 포트 없음, MCP UID 1000과 두 컨테이너의 읽기 전용 루트 파일시스템을 확인했다. 별도 시험 테이블의 값이 DB 컨테이너 재시작 후 유지됨을 확인한 뒤 시험 테이블을 제거했다. 비밀값 생성·재실행 보존·symlink 거부 3개 검사, 저장소 오프라인 검사 14개, 새 workflow actionlint도 통과했다. 이 결과는 사용자 AWS 검증이나 백업 복구 시험을 대신하지 않는다.

## 5. 다음 연결 순서

사용자가 AWS의 `PASS: private bootstrap checks`를 보고한 뒤, 실제 카탈로그 재생성·별도 MCP 연결 절차를 [30 문서](30-private-real-catalog-connection.md)에 추가했다. 기존 fixture·DB를 교체하지 않는다.

1. AWS에서 위 비공개 기동·도구·DB 검사 결과를 확인한다.
2. v2 카탈로그를 권리·출처 검토 범위 안에서 서버에 생성하고 읽기 전용 마운트와 v2 SDK 검사를 추가한다. 파일 경로만 바꿔 v1 fixture 검사를 v2 통과로 해석하지 않는다.
3. Langflow 이미지·버전·전용 상태 DB·별도 계정·암호화 키 영속성을 검증하고 현재 로컬 설정과 별도 운영 설정을 만든다.
4. [27 최종 검증](27-agent-catalog-v2-validation.md)을 연결하며 [28 평가](28-agent-v2-evaluation.md)의 실패·문맥 계약 문제를 먼저 개선한다. 실제 모델·비용·보안 평가는 각각 따로 기록한다.
5. 제한 Gateway와 Cloudflare Access·Tunnel 인증을 검증한 뒤 Vercel Preview로 연결한다. 현재 bootstrap 컨테이너를 그대로 공개 Agent로 노출하지 않는다.

전체 운영 설계: [21 Docker](21-linux-docker-production.md), [22 방어 경계](22-jailbreak-and-prompt-injection-defense.md). Word/PDF는 이번 단계에서 재생성하지 않았으며 최신 실행 절차는 본 문서와 배포 파일을 따른다.
