# AWS MCP에 실제 981곳 카탈로그 연결

2026-11-19 한국 시간. 사용자가 AWS의 Docker·MCP 도구·인증·PostgreSQL·pgvector 검사를 통과했다고 보고했다. 이번 변경은 [29 초기 구성](29-lightsail-docker-bootstrap.md)의 가상 장소를 기존 앱의 **981곳 JSON 카탈로그**로 교체하는 실행 절차다. AWS에 직접 접속해서 적용한 결과는 아니다.

## 1. 기존 AWS SSH 터미널에서 실행

29 문서대로 만든 `oreumit-agent` 폴더에서 실행한다. Node 설치나 모델 API 키는 필요 없다. Docker·Compose·Git·Python 3을 사용한다. 아래 명령은 한 줄씩 실행하고 실패하면 다음 줄을 진행하지 않는다.

```bash
cd ~/oreumit-agent
git status --short
git fetch origin refs/heads/codex/lightsail-real-catalog:refs/remotes/origin/codex/lightsail-real-catalog
git switch --no-track -c codex/lightsail-real-catalog origin/codex/lightsail-real-catalog
sudo python3 deploy/export-catalog.py
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml config --quiet
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml up -d --no-deps --wait --wait-timeout 120 jeju-mcp
sudo bash deploy/check-catalog.sh
```

`git status --short`에 수정 파일이 보이면 기존 작업을 먼저 확인한다. 강제 초기화나 파일 삭제로 진행하지 않는다. 위 `git switch -c`는 최초 전환용이다. 이미 이 브랜치를 만들었다면 `git switch codex/lightsail-real-catalog`를 쓴다. 이전 `--single-branch` clone의 제한된 fetch 설정도 지원하도록 원격 ref와 시작점을 명시했다. 브랜치는 검토용이며 main에 자동 병합하지 않는다. 기존 서버에서 별도의 Compose 프로젝트 이름(`-p` 또는 `COMPOSE_PROJECT_NAME`)을 사용했다면 같은 이름을 유지해야 한다. 기본 이름은 `oreumit-bootstrap`이다.

처음 내보낼 때 Docker 이미지·고정 앱 소스·npm 의존성을 내려받으므로 몇 분 걸릴 수 있다. 소스는 `OREUM-IT/jeju-tour@b0602a175c1d08959b7187da2b29ac450b52e0ba`로 고정한다. 현재 웹앱을 호출하거나 사용자 설문·모델 API를 보내는 작업이 아니다. 내보내기 중 오류가 나도 기존 fixture MCP는 계속 실행된다.

## 2. 성공 확인

마지막 검사에서 다음 두 결과를 확인한다. 출력에는 토큰이나 장소 원문이 없다.

- MCP SDK: `status: passed`, `dataMode: app-export`, 세 도구 호출 성공.
- `PASS: missing token rejected; authenticated v2 catalog readiness; 981 places; host SHA-256 matches; productionReady=false`.

`productionReady=false`는 오류가 아니다. 실제 장소를 읽어도 실내·휠체어·영업시간·비용을 확정할 검증 근거는 아직 0건이고, Langflow·추천 Agent·Gateway·Tunnel·Vercel은 이 구성으로 연결되지 않는다. PostgreSQL에 장소나 벡터를 넣는 단계도 아니다.

## 3. 파일·인증·DB 보존

- [export-catalog.py](../deploy/export-catalog.py)는 일회용 Docker 컨테이너에서 참고 앱의 별도 사본을 빌드하고 기존 [덤프·내보내기 절차](19-agent-step2-data-foundation.md)를 실행한다. `.local` 안의 JSON만 호스트로 남고 컨테이너는 종료 후 제거된다. 실행기 이미지에는 장소 데이터·인증 파일을 넣지 않는다.
- 결과는 `deploy/.local/catalog/agent-catalog-b0602a1.json`이다. 디렉터리는 0700, 이 **비밀값이 아닌 카탈로그 파일만** 0644로 둬 컨테이너의 UID 1000이 읽을 수 있게 한다. 파일 하나만 읽기 전용으로 마운트하며 비밀 설정 폴더를 마운트하지 않는다. Git에 포함하지 않는다.
- 재실행은 기존 결과의 출처·건수·파일 구조를 확인하고 보존한다. 임의 덮어쓰기·토큰 재생성·DB 초기화를 하지 않는다. 새로 생성된 파일은 내보내기 및 검증이 성공한 뒤에만 최종 경로에 게시한다.
- 기존 `mcp.env`, `db.env`, Compose 프로젝트 이름, DB 볼륨을 유지한다. `up --no-deps ... jeju-mcp`는 MCP만 재생성한다. 서비스 교체 중 짧은 MCP 중단은 발생한다.
- MCP 포트는 계속 `127.0.0.1:8788`, DB는 외부 포트 없음, 네트워크는 기존 private 설정이다. 생성기의 GitHub·npm 다운로드만 네트워크를 사용한다.

## 4. 이후 재시작과 되돌리기

카탈로그 적용 뒤에는 **항상 두 Compose 파일**을 사용한다. 기본 파일만 사용하면 fixture 모드로 돌아간다. 새 검사는 `check-catalog.sh`이며, 기존 `check.sh`는 fixture 초기 구성 전용이다.

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml ps
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml up -d --no-deps --wait --wait-timeout 120 jeju-mcp
sudo bash deploy/check-catalog.sh
```

실제 카탈로그 기동에 실패하면 파일·권한·로그를 확인한다. 기존 가상 데이터 검사 상태로 되돌려야 할 때만 다음을 실행한다. DB·인증·생성된 카탈로그는 보존한다.

```bash
sudo docker compose -f deploy/compose.yaml up -d --no-deps --wait --wait-timeout 120 jeju-mcp
sudo bash deploy/check.sh
```

`down --volumes`는 사용하지 않는다. 전체 `docker compose config`, 환경변수가 포함된 `docker inspect`, `.env` 내용도 공유하지 않는다. 확인에 필요한 출력은 마지막 PASS와 오류 문장뿐이다.

## 5. 검증과 다음 단계

검토 브랜치의 Docker CI는 fixture·DB 검사 후 고정 소스에서 카탈로그를 생성하고, 같은 MCP를 v2로 교체해 실제 SDK·인증·981곳·파일 해시를 검사한다. 원문 카탈로그를 CI artifact로 업로드하지 않는다. CI 통과와 사용자 AWS 적용 성공은 별도로 확인한다.

AWS의 위 검사까지 성공하면 다음은 **Langflow 전용 상태 DB·인증·암호화 키 영속성 → 기존 Vertex AI 모델 연결 → 추천 최종 검증 → 인증 Gateway·Tunnel → Vercel Preview**다. 기존 pgvector bootstrap 관리자 계정을 Langflow와 공유하지 않는다. [28 문서](28-agent-v2-evaluation.md)의 실패·문맥 조건 한계도 남아 있으므로 연결 성공을 추천 품질 완료로 표시하지 않는다.
