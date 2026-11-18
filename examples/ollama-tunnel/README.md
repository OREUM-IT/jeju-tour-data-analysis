# Windows·macOS용 Ollama 임시 터널 연결 예제

이 예제는 **연결 시험용**이다. 관광 추천 Agent·MCP 서버·운영용 작업 큐를 구현한 것은 아니다. OpenAI/Claude API를 호출하거나 유료 서비스를 등록하지 않는다. 두 PC에서 각각 실행하면 각기 다른 URL과 인증 토큰을 갖는다.

구조: `본인의 확인 스크립트 → 임시 HTTPS 터널 → 인증 Gateway(127.0.0.1:8787) → Ollama(127.0.0.1:11434)`.

## 1. 장비마다 준비

- [Ollama](https://ollama.com/download)를 Windows/macOS용으로 설치하고 실행한다. macOS는 GPU 사용을 위해 네이티브 앱을 우선 사용한다.
- [Node.js](https://nodejs.org/en/download)의 현재 지원되는 LTS를 설치한다. 이 예제는 `--env-file`을 지원하는 Node 버전이 필요하며 별도 npm 패키지 설치는 없다.
- [cloudflared 설치 안내](https://developers.cloudflare.com/tunnel/downloads/)에 따라 OS·아키텍처에 맞게 설치한다.
- Windows에서는 PowerShell, macOS에서는 Terminal을 사용한다. Ollama·Node·cloudflared를 같은 OS에서 실행한다. Windows 앱과 WSL/Docker의 `localhost`를 섞지 않는다.
- 이 폴더를 내려받거나 ZIP을 풀고 해당 폴더에서 아래 명령을 실행한다.

설치 확인:

```sh
node --version
cloudflared --version
ollama --version
```

공식 설치 안내에 해당 패키지가 표시되는 경우 다음 방법을 사용할 수 있다.

Windows PowerShell:

```powershell
winget install --id Cloudflare.cloudflared --exact
```

macOS에서 Homebrew를 이미 사용하는 경우:

```sh
brew install cloudflared
```

## 2. 모델을 로컬에서 먼저 확인

설치된 모델이 있다면 `ollama list`의 정확한 태그를 사용한다. 새로 시험할 때 다음은 작은 모델 계열의 연결 시험 예시이며, 제주 추천의 최적 모델이나 장비별 성능을 보증하지 않는다. 모델 다운로드에는 디스크·네트워크 자원이 필요하다.

```sh
ollama pull llama3.2:3b
ollama list
ollama run llama3.2:3b "안녕이라고 한 단어로 답해줘."
```

이미 실행 중인 Ollama 앱이 있다면 `ollama serve`를 중복 실행하지 않는다. API는 기본 `127.0.0.1:11434`를 사용한다. 외부 접속을 위해 `OLLAMA_HOST=0.0.0.0` 또는 공유기 포트 포워딩을 설정할 필요가 없다. Cloud 모델 태그를 로컬 전용 실행과 혼동하지 않는다.

## 3. Gateway 설정과 실행 — 터미널 A

설치된 정확한 태그를 인자로 전달한다. 예:

```sh
node configure.mjs llama3.2:3b
node --env-file=.env.local gateway.mjs
```

`configure.mjs`는 로컬 모델 목록을 확인하고 `.env.local`에 임의 토큰과 모델명을 저장한다. 토큰을 화면에 출력하지 않으며 기존 파일을 덮어쓰지 않는다. 이 파일은 각 PC에서 별도로 생성하고 채팅·Git·공유 ZIP에 포함하지 않는다. Windows에서는 파일이 본인 계정의 보호된 폴더에 있는지도 확인한다.

Gateway는 같은 호스트의 loopback에만 바인딩한다. 모델 관리 API는 차단하고, 정해진 모델의 짧은 non-streaming 대화 한 건만 동시에 허용한다. 응답 예산은 256토큰, 컨텍스트는 4096, 업스트림 시간은 25초인 연결 시험 설정이다. 장비에서 느리면 로컬 추론부터 확인한다. 이 제한을 근거로 긴 Agent 작업을 동기 요청으로 운영하지 않는다.

## 4. 로컬 연결 시험 — 터미널 B

```sh
node --env-file=.env.local check.mjs
```

토큰 없는 요청의 401, 인증된 health 응답, 모델 다운로드 경로의 404, 실제 짧은 모델 응답을 확인한다. `/healthz`만 성공한 것은 Ollama 추론 성공이 아니다. 토큰은 명령행에 직접 입력하지 않는다.

## 5. Quick Tunnel 실행 — 터미널 C

```sh
cloudflared tunnel --url http://127.0.0.1:8787
```

출력된 `https://임의이름.trycloudflare.com` 주소가 **이 장비의 임시 주소**다. Gateway를 가리키는 8787을 사용한다. 원본 Ollama 11434를 직접 연결하지 않는다. 설치된 cloudflared에 기존 config 파일이 있어 Quick Tunnel이 실행되지 않으면 기존 터널 설정을 지우지 말고 공식 Quick Tunnel 안내의 config 제한을 확인한다.

터널은 중지·재시작하면 주소가 바뀔 수 있고 가용성 보장이 없다. Quick Tunnel은 SSE를 지원하지 않으므로 이 예제는 `stream:false`만 사용한다. PC 절전·재부팅·네트워크 단절 시 연결도 중단된다. 임시 공개 URL이므로 실제 개인 여행 데이터 대신 짧은 시험 문장만 사용한다.

## 6. 원격 연결 시험 — 터미널 B

아래 주소를 **본인이 위에서 만든 URL**로 교체한다. 다른 사람이 제공한 터널 URL로 자신의 토큰을 보내지 않는다.

```sh
node --env-file=.env.local check.mjs https://YOUR-OWN-TUNNEL.trycloudflare.com
```

스크립트는 리다이렉트를 따라가며 토큰을 전달하지 않는다. 이 검사는 호스트에서 Cloudflare를 거친 왕복 연결만 확인하며, 실제 Vercel 서버에서의 호출·사용자 권한·과금/큐 처리가 검증된 것은 아니다.

Windows와 macOS에서는 각각 이 과정을 실행하고 URL/토큰을 별도로 관리한다. 같은 포트 번호를 각 PC에서 쓰는 것은 충돌하지 않는다. 두 장비는 모델·자원·가용성이 다르므로 같은 터널의 replica로 묶어 임의 분산하지 않는다.

## 7. Vercel 앱과 MCP 연결의 다음 단계

현재 앱 소스에는 이 Gateway를 호출하는 Ollama 어댑터가 없다. 환경변수만 등록하거나 Redeploy하는 것으로 화면에서 Ollama를 선택할 수 있게 되지 않는다. 후속 서버 어댑터가 읽도록 설계할 변수는 예를 들어 `OLLAMA_WINDOWS_BASE_URL`, `OLLAMA_WINDOWS_GATEWAY_TOKEN`, `OLLAMA_MAC_BASE_URL`, `OLLAMA_MAC_GATEWAY_TOKEN`이다. 아직 구현된 변수 계약이 아니다.

실제 연결에서는 서버만 `Authorization: Bearer ...`를 전송하고, 브라우저는 기존 Vercel API를 호출한다. 로그인·사용량 한도·장비별 큐·재시도/취소·출처 검증을 서버에 추가한다. 이 예제의 `/api/chat`은 **Ollama HTTP API**이며 MCP `/mcp`가 아니다. 제주 관광 MCP 도구는 별도 서버에서 Worker가 호출한다.

운영용으로 전환할 때는 사용자 소유 도메인의 고정 Named Tunnel 또는 안정적인 서버 주소, 서비스 인증, 감시·재시작·장시간 작업 처리를 구성한다. `jeju-tour-six.vercel.app` 자체를 사용자 소유 DNS 영역으로 Cloudflare에 등록할 수는 없다.

## 오류와 종료

| 증상 | 확인할 내용 |
|---|---|
| 로컬 모델 목록을 읽지 못함 | Ollama 앱 실행·11434·같은 OS/네트워크 환경·모델 설치 여부 |
| 401 | `.env.local`이 해당 장비 Gateway와 같은 토큰인지 확인. 키 값을 채팅에 보내지 않음 |
| 400 / 413 | 제한된 메시지 형식·정확한 모델 태그·작은 입력 크기 사용 |
| 429 | 현재 한 요청이 실행 중. 완료 후 수동 재시험 |
| 502 / 504 | 로컬 Ollama 상태·모델 예열·메모리·시간 초과 확인 |
| 터널 연결 불가 | cloudflared 프로세스·실제 생성된 URL·외부 연결 정책·절전 상태 확인 |

중지하려면 터널 터미널에서 Ctrl+C, Gateway 터미널에서 Ctrl+C를 누른다. 터널을 닫아도 로컬 모델 파일은 삭제되지 않는다. 토큰을 바꿀 때는 연결된 호출자 설정도 함께 갱신한다.

## 검증 범위

저장소의 `node --test gateway.test.mjs`는 가짜 로컬 업스트림으로 Gateway의 계약을 검증한다. 실제 모델·사용자 PC·Cloudflare·Vercel 연결은 위 명령을 사용자 장비에서 실행해야 확인할 수 있다.
