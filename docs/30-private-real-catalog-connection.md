# 실제 제주 장소 981곳을 비공개 MCP에 연결

기준일: 2026-11-19 한국 시간. 사용자가 AWS에서 [29 Docker 기동](29-lightsail-docker-bootstrap.md)의 `PASS: private bootstrap checks` 출력을 확인한 뒤 실제 연결 방법을 요청했다. 이 단계는 **고정 앱 소스로 카탈로그 재생성 → 별도 MCP 기동 → 프로토콜·근거·최종 판정 검사**다. AWS에 직접 SSH로 접속한 것이 아니며 사용자 실행 결과로 최종 확인해야 한다.

## 1. 유지하는 것과 새 연결

| 구성 | 동작 |
|---|---|
| 기존 `jeju-mcp` | 127.0.0.1:8788의 가상 fixture를 유지 |
| 기존 PostgreSQL·pgvector | 컨테이너·볼륨·비밀번호 유지. 이번 단계에서 관광 테이블/임베딩 적재 없음 |
| 새 `jeju-mcp-v2` | 127.0.0.1:8789에서 981곳 JSON을 읽기 전용으로 사용 |
| 새 서비스 토큰 | `deploy/.local/v2.env`에 별도 생성. 기존 토큰·DB 비밀번호는 재생성하지 않음 |
| 검색·조건 | 문자열·테마·권역 검색과 근거 조건 검사. 아직 벡터 검색·모델 추천이 아님 |

`v2`는 **카탈로그 스키마 2.0**이다. MCP SDK 1.32.1·Streamable HTTP는 그대로다. 서로 다른 내부 네트워크를 사용하고 Docker socket·호스트 홈·AWS 자격·모델 키를 마운트하지 않는다. 인터넷에 8788/8789/5432를 여는 작업도 없다.

## 2. AWS SSH에서 한 줄씩 실행

기존 `codex/lightsail-docker-bootstrap` 브랜치의 첫 설치를 기준으로 한다. `git pull --ff-only`가 충돌·분기·로컬 변경으로 실패하면 중단하고 확인한다. 기존 폴더를 삭제하거나 강제 reset하지 않는다.

```bash
cd ~/oreumit-agent
git pull --ff-only
python3 deploy/prepare.py
sudo bash deploy/prepare-catalog.sh
sudo bash deploy/start-catalog.sh
sudo bash deploy/check-catalog.sh
```

- 자료 준비는 첫 실행에 GitHub 소스와 별도 Node 빌드 이미지를 다운로드하므로 시간이 걸린다. API 키·추가 유료 모델 계정은 필요 없다. 서버·전송량 비용은 기존 요금제에 따른다.
- `prepare-catalog.sh`의 성공 문구는 `PASS: real catalog prepared`다. 기존 카탈로그가 있으면 스키마·고정 출처·건수·근거 등급을 검사하고 덮어쓰지 않는다.
- `start-catalog.sh`는 새 서비스만 빌드·기동한다. 기존 fixture·DB를 재생성하거나 볼륨을 삭제하지 않는다. 자료 파일이 없으면 시작하지 않는다.
- 최종 성공 문구는 **`PASS: real catalog v2 connection checks`**다. `places:981`, `schemaVersion:2.0`, `dataMode:app-export`, `constraintChecks:3924`, `allConstraints:unknown`, `productionReady:false`가 함께 나온다.

`unknown`은 오류가 아니라 필수 조건을 입증할 근거가 부족하다는 뜻이다. 실제 장소 이름이 있어도 가격·실내·접근성·영업시간을 확인 완료로 바꾸지 않는다. 실제 최신 관광 데이터·재배포/상업 이용 허가를 새로 검증한 단계도 아니다.

## 3. 자료 생성 과정과 보관

1. 사용자 서버의 `deploy/.local/catalog-build.*` 아래 **새 scratch checkout**에 `OREUM-IT/jeju-tour@b0602a175c1d08959b7187da2b29ac450b52e0ba`를 받는다. 기존 참고 checkout이나 원본 앱에는 수정·push하지 않는다.
2. `git archive`로 별도 빌드 사본을 만든다. 잠금파일로 `npm ci --ignore-scripts`를 실행하고 서버만 빌드한다. 앱의 lifecycle script와 외부 모델은 실행하지 않는다.
3. 두 번째 컨테이너는 **`--network none`**으로 실행한다. 앱을 컨테이너 내부 loopback에만 띄워 기존 `dump-app-places.mjs`로 목록을 읽고, `export-agent-catalog.mjs`가 원시 파일 바이트와 고정 커밋을 대조해 v2를 만든다.
4. 스키마·981곳·원시 데이터 해시·앱 목록 해시·검증 완료 속성 0건을 확인한 뒤 `deploy/.local/catalog/`에 놓는다. 서버에는 `agent-catalog.json` 한 파일만 읽기 전용으로 마운트한다.

마운트하는 도구 소스·테마 매핑은 명시적인 파일/폴더 목록이다. 전체 분석 저장소나 `.env`는 빌드 컨테이너에 전달하지 않는다. 자료 생성용 컨테이너는 임시이며 모델·AWS·Vercel 인증을 공급하지 않는다. 관리형 개발 환경에서 필요한 경우 공개 세션 CA만 읽기 전용 마운트하며 Docker에 설정된 프록시를 그대로 사용한다. TLS 검증은 끄지 않는다.

카탈로그·앱 덤프·원본 사본·실패 로그는 `.local` 아래 비공개로 보관하고 Git·공개 CI·Word/PDF·공유 ZIP에 넣지 않는다. 부모 `.local`은 0700이며 MCP가 읽을 비밀값 없는 카탈로그 파일만 0644다. 비밀값 파일은 0600을 유지한다. 원본 자료가 저장소에 있다는 이유로 이용권을 확장하지 않는다. 외부 모델 전송과 인터넷 공개는 후속 검토 범위다.

실패한 자료 준비 폴더는 진단을 위해 보존하며 재시도는 새 폴더를 만든다. 반복 실패 시 디스크 사용량을 확인한다. 성공한 카탈로그를 업데이트할 때는 기존 파일을 자동 덮어쓰지 않고 새 버전 생성·검사·전환·복귀 절차를 별도로 마련해야 한다.

## 4. 검사 내용과 한계

[check-v2.mjs](../services/jeju-mcp/scripts/check-v2.mjs)는 아래를 검사하며 장소 이름·ID·원문·비밀값을 출력하지 않는다.

- 인증 없는 readiness 401과 인증된 스키마 2.0·981곳·적재 파일 해시 일치.
- 실제 MCP SDK 도구 발견·검색·상세 조회.
- 전체 981곳 × 실내/휠체어/예산/영업시간 4조건 = 3,924개 `unknown` 판정.
- `/prepare-request`와 `/validate-response`의 확인 필요 응답 수락, 거짓 `pass`와 가짜 ID 차단.

이 검사는 LLM을 호출하지 않는다. 기존 [96문항 평가](28-agent-v2-evaluation.md)를 재실행하거나 품질 실패를 해결했다는 뜻이 아니다. 전체 탈옥 평가·부하·백업 복원도 별도다. 공개 CI에서는 실제 장소 자료를 다운로드하지 않으며 기본 fixture 기동과 스크립트 구문·설정 검사를 수행한다.

2026-11-19 Codex Docker에서 고정 커밋을 새로 fetch·archive해 981곳을 재생성했고 기존 원시·앱 목록 해시와 일치했다. 자료 준비 재실행은 기존 파일을 보존하며 검증만 했다. 새 v2 컨테이너 healthy, 위 SDK·3,924조건·최종 수락/차단 검사는 전부 통과했고 모델 호출은 0이었다. MCP 이미지의 npm 단계에는 29 문서와 같은 임시 BuildKit CA 마운트를 사용했으며 TLS 검증을 유지했다. 사용자 AWS와 원본 Dockerfile의 기동은 해당 서버의 최종 출력으로 별도 확인한다.

## 5. 문제 확인과 다음 단계

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml ps -a
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml logs --tail 50 jeju-mcp-v2
```

기존 서비스에 영향을 주지 않고 새 v2만 중지하려면 다음을 실행한다.

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.catalog.yaml stop jeju-mcp-v2
```

이후 Langflow의 별도 운영 설정·상태 DB·인증·최종 검증 흐름을 연결한다. 컨테이너의 `127.0.0.1`은 각자 자신이므로 현재 로컬 흐름 URL을 그대로 사용하지 않는다. Docker 서비스명 통신과 SSRF 허용·거부 검사, 모델별 인증·비용 계측, 제한 Gateway가 준비된 뒤 Tunnel·Access와 Vercel Preview를 연결한다. 지금은 `oreumit.com` DNS·운영 웹앱·Cloudflare에 변경이 없다.
