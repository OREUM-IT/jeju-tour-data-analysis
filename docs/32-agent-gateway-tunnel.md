# 제한 Agent Gateway와 Cloudflare Access·Tunnel 연결

2026-11-21 최신 변경은 **[모델 호출 예산 조정](#모델-호출-예산-조정)**이다. Gateway 흐름의 모델 호출을 8회로 늘리고 마지막 호출을 답변 전용으로 바꾼다. PR #21·#22는 main에 병합됐고, 2026-11-21 사용자가 AWS에서 `main@ce2f657` 적용 성공 출력을 제공했다(모델 미실행). 실제 웹 요청의 차단 감소는 아직 확인하지 않았다. 직전 변경은 **[요청 큐와 동시 처리 업데이트](#요청-큐와-동시-처리-업데이트)**다. 기존 PR #19의 후보 복구를 포함해 MCP·Gateway를 함께 갱신한다. 이미 연결된 AWS에서는 아래 최초 설치·Langflow bootstrap을 반복하지 않는다. 기본 동시 2건은 설정·코드 상한이며, 사용자 AWS 8GB에서 실제 모델 2건을 동시에 실행해 성능을 확인한 결과는 아니다.

사용자 AWS에서 **981곳 MCP → Langflow → Vertex 단건 응답·최종 검증 성공**에 이어 **Gateway·Cloudflare HTTPS·Access 서비스 인증·앱 HMAC/JWT의 공개 준비 검사(`scope:public`)까지 통과**했다. PR #16은 `main@2b44228`에 병합됐고 공개 연결 수정은 PR #17의 `7890060`으로 적용됐다. 다음은 Vercel 서버 어댑터·서버 비밀 설정·Preview 연결이다. 공개 준비 검사는 모델을 실행하지 않으며 기존 성공한 Vertex 단건 실행이나 96문항 평가를 반복하지 않는다.

```mermaid
flowchart LR
  V[Vercel 서버] -->|HTTPS + 특정 Access 서비스 토큰 + 앱 HMAC| C[Cloudflare Access]
  C --> T[cloudflared]
  T -->|gateway_ingress| G[제한 Gateway :8790]
  G --> L[고정 Langflow 흐름]
  G --> M[요청 범위가 제한된 제주 MCP]
  L --> M
```

공개 대상은 **`agent.oreumit.com` 한 호스트의 Gateway**다. Langflow 관리 화면·MCP·PostgreSQL은 공개 경로가 아니다. `oreumit.com`, `www.oreumit.com`의 기존 DNS나 Vercel 연결은 수정하지 않는다. AWS 방화벽에 8790·7860·8788·5432의 인바운드 포트를 추가하지 않는다. Tunnel은 외부로 연결하며, Gateway 호스트 포트는 `127.0.0.1:8790`이다.

## 1. AWS에서 비공개 Gateway 준비

기존 저장소 `~/oreumit-agent`, Compose 프로젝트 `oreumit-bootstrap`을 사용한다. 아래 새 브랜치는 최초 전환용이다. 이미 같은 로컬 브랜치가 있으면 `git switch codex/lightsail-agent-gateway` 후 `git merge --ff-only origin/codex/lightsail-agent-gateway`를 사용한다. 수정 파일이 있으면 강제 덮어쓰지 않는다.

```bash
cd ~/oreumit-agent &&
git status --short &&
test -z "$(git status --porcelain)" &&
git fetch origin refs/heads/codex/lightsail-agent-gateway:refs/remotes/origin/codex/lightsail-agent-gateway &&
git switch --no-track -c codex/lightsail-agent-gateway origin/codex/lightsail-agent-gateway
```

같은 SSH 터미널에서 다음을 실행한다. Gateway용 요청 범위·키·별도 흐름을 추가하기 위해 **MCP와 Langflow만 한 번 재생성**한다. 기존 DB·볼륨·카탈로그와 기존 Vertex 자격증명은 보존한다. 마지막 검사는 모델을 호출하지 않는다.

```bash
gateway_compose=(sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose
  -f deploy/compose.yaml -f deploy/compose.catalog.yaml
  -f deploy/compose.langflow.yaml -f deploy/compose.gateway.yaml)

sudo python3 deploy/prepare-gateway.py --init &&
"${gateway_compose[@]}" config --quiet &&
"${gateway_compose[@]}" up -d --build --no-deps --force-recreate --wait --wait-timeout 180 jeju-mcp &&
"${gateway_compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 300 langflow &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/prepare-gateway.py --provision &&
"${gateway_compose[@]}" up -d --build --no-deps --wait --wait-timeout 90 agent-gateway &&
"${gateway_compose[@]}" exec -T agent-gateway node check.mjs --internal
```

기대 결과는 Gateway `status:passed`, `mode:private`, `ready:true`, `catalogPlaces:981`, `modelExecuted:false`, `productionReady:false`다. `healthz`와 Docker의 Healthy는 프로세스 생존만 뜻한다. `check.mjs --internal`은 실제 고정 Langflow 흐름의 해시·실행 키, MCP 카탈로그·호출 제한과 비인증 경로 거부를 별도로 검사한다.

`--provision` 실패로 `.gateway-provisioning`이 남으면 자동 재시도하거나 키·파일을 삭제하지 않는다. 기존 Langflow의 전용 흐름/키와 설정을 확인해야 한다. 정상 완료 후 같은 준비 명령은 기존 키를 보존한다. 키·환경 파일·일반 `docker compose config` 출력은 공유하지 않고 마지막 집계 JSON·고정 오류 코드만 공유한다.

### 새 Gateway 경로의 실응답 한 건

내부 준비 검사가 통과하면 아래를 **한 번만** 실행한다. 기존 AWS 단건 실행과 달리 새 Gateway 인증·요청 범위 도구·최종 검증을 모두 거치는 요청이다. 기존 Vertex API 사용료가 발생할 수 있다. 실패하면 결과를 먼저 확인하고 자동 반복하지 않는다.

```bash
"${gateway_compose[@]}" exec -T agent-gateway node check.mjs --run-once
```

기대 결과는 `status:passed`, `finalValidationAccepted:true`, `action:cannot_confirm`이며 실내 근거가 없는 후보에 확인 안내가 유지된다. 모델 호출 횟수·비용을 실측한 출력은 아니다. 최초 Gateway의 한도는 동시 1개·주체당 시간당 6개·전체 시간당 30개였다. 요청 큐 적용 후의 한도는 [최신 업데이트 절](#요청-큐와-동시-처리-업데이트)을 따른다. 도구 8회, 출력 2048토큰, 요청 실행 예산 105초와 범위 폐기 최대 3초는 유지한다. 모델 호출은 처음 4회였고 [모델 호출 예산 조정](#모델-호출-예산-조정)을 적용하면 8회(마지막 호출은 답변 전용), 되돌리면 다시 4회다. PR #21 이후 새로 설치하면 처음부터 8회 흐름이 만들어지며, 4회 빌드는 그 전에 설치한 서버나 되돌린 뒤에만 있다. 시간 초과나 불확실한 실행 뒤에는 해당 실행 슬롯의 보호 시간을 유지한다. Gateway readiness는 모델을 실행하지 않는다.

## 2. Cloudflare Zero Trust와 전용 서비스 토큰 만들기

이 절은 계정 소유자가 [Cloudflare 대시보드](https://dash.cloudflare.com/)에서 진행한다. 현재 계정은 도메인 관리만 설정돼 있으므로 **Zero Trust** 최초 설정이 필요하다. 팀 이름과 요금제를 선택한다. 공식 온보딩 안내는 Free 요금제도 결제정보 입력 단계가 있을 수 있다고 설명한다. 기존 도메인의 DNS 레코드는 건드리지 않는다.

1. **Zero Trust → Access controls → Service credentials → Service Tokens → Create Service Token**을 연다.
2. 이름 `jeju-vercel-gateway`, 운영에 맞는 만료 기간을 선택하고 **Generate token**을 누른다.
3. **Client ID와 Client Secret을 비밀 저장소에 보관**한다. Secret은 생성 시에만 표시된다. 채팅·GitHub·문서에는 붙이지 않는다.
4. AWS SSH 터미널에서 아래 두 입력에 각각 붙인다. 화면에 입력값이 표시되지 않는다. `sudo -v`로 먼저 인증한 뒤 stdin으로만 전달한다.

```bash
cd ~/oreumit-agent
set +x
sudo -v &&
read -rsp 'Access Client ID: ' access_client_id && printf '\n' &&
read -rsp 'Access Client Secret: ' access_client_secret && printf '\n' &&
printf '%s\n%s\n' "$access_client_id" "$access_client_secret" | sudo python3 deploy/tunnel/configure.py service-token
unset access_client_id access_client_secret
```

저장은 `deploy/.local/tunnel/service-token.env`, root 소유 0600이다. 파일이나 값은 컨테이너에 마운트하지 않으며 나중에 공개 `/readyz` 인증 검사에 사용한다. 새 `cfast_` 형식과 기존 64자리 hex Secret을 지원한다.

## 3. 공개 경로보다 먼저 Access 정책·앱 만들기

1. **Zero Trust → Access controls → Policies → Add a policy**에서 이름을 `jeju-vercel-service-only`로 지정한다.
2. Action은 **Service Auth**를 선택한다. Include 조건은 **Service Token → `jeju-vercel-gateway`** 하나로 지정한다. **Any Access Service Token, Everyone, Bypass**를 선택하지 않는다.
3. **Access controls → Applications → Create new application → Self-hosted and private → Add public hostname**으로 이동한다.
4. 앱 이름 `jeju-agent-gateway`, 공개 호스트 **`agent.oreumit.com`**을 설정하고 **Path는 비운다**. `/v1/recommend`에만 보호를 걸면 다른 경로가 남으므로 전체 호스트를 보호한다.
5. 앞서 만든 서비스 전용 정책만 연결하고 **Create**로 저장한다.
6. 앱 **Edit → Additional settings → Application Audience (AUD) Tag**에서 AUD를 복사한다.
7. **Zero Trust → Settings → Team name and domain → Team name**에서 팀 이름을 확인한다. issuer는 **`https://<팀이름>.cloudflareaccess.com`**이다.

issuer와 AUD는 공개 설정값이다. 아래 예시의 팀 이름과 AUD만 실제 값으로 바꾼다. 아직 Tunnel route를 만들지 않는다.

```bash
sudo python3 deploy/tunnel/configure.py access \
  --issuer 'https://YOUR-TEAM.cloudflareaccess.com' \
  --audience 'YOUR_APPLICATION_AUD_64_LOWERCASE_HEX' \
  --hostname 'agent.oreumit.com' &&
sudo python3 deploy/tunnel/check.py --scope config
```

설정 검사 `scope:config`는 파일 형식·권한만 검사하며 Gateway control token이나 Tunnel token이 없어도 가능하다. Gateway가 준비됐다는 뜻은 아니다. 별도 `gateway-access.env`만 생성하고 기존 `gateway.env`의 HMAC·MCP·Langflow 비밀값과 `GATEWAY_MODE=private`는 바꾸지 않는다. 최종 Tunnel overlay가 Access 설정을 마지막으로 읽고 `GATEWAY_MODE=access`를 강제한다.

다음은 **Gateway만** access 모드로 재생성한다. Tunnel 파일이 아직 없으므로 이 단계에 전체 `up`이나 `--wait` 대상 생략을 사용하지 않는다.

```bash
tunnel_compose=(sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap docker compose
  -f deploy/compose.yaml -f deploy/compose.catalog.yaml
  -f deploy/compose.langflow.yaml -f deploy/compose.gateway.yaml
  -f deploy/compose.tunnel.yaml)

"${tunnel_compose[@]}" config --quiet &&
"${tunnel_compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 90 agent-gateway &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/tunnel/check.py --scope gateway
```

`scope:gateway`, `status:passed`가 나와야 다음으로 간다. 이 검사는 실행 중인 컨테이너의 issuer·AUD·host·mode가 파일과 일치하고 내부 준비 검사도 통과하는지 확인한다. Access JWT 없이 사용할 수 있는 HTTP 인증 우회 경로는 만들지 않는다.

## 4. Named Tunnel 만들고 connector만 시작

1. [Cloudflare 대시보드](https://dash.cloudflare.com/)에서 **Networking → Tunnels → Create a tunnel**을 연다. 계정 UI에 이전 메뉴가 보이면 Zero Trust의 **Networks → Connectors → Cloudflare Tunnels**에 해당한다.
2. 이름을 `jeju-agent-aws`로 지정하고 **Create Tunnel**을 누른다. connector 유형 선택 화면이 나오면 **Cloudflared**를 선택한다.
3. Docker 설치 화면에서 **Tunnel token 값만** 복사한다. 토큰이 포함된 전체 `docker run ... --token ...` 명령을 셸에 붙이지 않는다.
4. 다음 숨김 입력으로 저장한다. 아직 Published application route는 만들지 않는다.

```bash
cd ~/oreumit-agent
set +x
sudo -v &&
read -rsp 'Tunnel token: ' tunnel_token && printf '\n' &&
printf '%s' "$tunnel_token" | sudo python3 deploy/tunnel/configure.py token
unset tunnel_token
```

원본은 `deploy/.local/tunnel/token` root:root 0600, 실행용 복사본은 `token.runtime` UID:GID 65532:65532·0400이다. 부모 `tunnel/`은 root:root 0700이다. Docker에는 실행용 **단일 파일만 읽기 전용**으로 마운트한다. settings 디렉터리 전체·Access 서비스 Secret·Gateway 키는 cloudflared에 전달하지 않는다. 표준 Cloudflare 공용 endpoint의 remotely managed token을 지원하며 별도 endpoint가 지정된 token은 거부한다.

같은 SSH 세션의 `tunnel_compose` 배열을 사용한다. 새 터미널이면 3절에서 배열 정의만 다시 실행한다. 이 기동은 cloudflared만 대상으로 하며 다른 서비스나 DB를 재생성하지 않는다.

```bash
sudo python3 deploy/tunnel/configure.py check &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/tunnel/check.py --scope gateway &&
"${tunnel_compose[@]}" up -d --no-deps --wait --wait-timeout 120 cloudflared &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/tunnel/check.py --scope connector
```

`scope:connector`, `status:passed`는 Gateway 내부 준비와 cloudflared의 Cloudflare 연결을 확인한다. Published application route·Access 정책까지 확인한 결과는 아니다. 호스트 인바운드를 열지 않아도 동작한다. AWS 아웃바운드를 제한했다면 [Cloudflare Tunnel 방화벽 요건](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/)의 TCP/UDP 7844, DNS와 Gateway JWKS용 HTTPS 접근을 확인한다.

## 5. route 추가 후 공개 인증 검사

위 Gateway access 모드와 connector 검사가 통과한 다음 진행한다. Access 앱과 Service Auth 정책이 전체 호스트에 연결된 것을 먼저 다시 확인한다.

1. **Networking → Tunnels → `jeju-agent-aws` → Routes → Add route → Published application**을 연다.
2. **Subdomain `agent`**, **Domain `oreumit.com`**, **Path 빈칸**을 지정한다.
3. Service URL에 **`http://agent-gateway:8790`**을 입력한다. Type과 URL이 분리된 화면이면 Type **HTTP**, URL **`agent-gateway:8790`**이다.
4. HTTP 설정의 **HTTP Host Header**가 있으면 **`agent.oreumit.com`**으로 지정한다. 기본값으로도 원래 공개 Host가 유지돼야 하며 내부 서비스 이름으로 바꾸면 Gateway가 거부한다.
5. **Add route**를 누른다. `agent` 이름의 기존 DNS 레코드와 충돌한다면 덮어쓰지 말고 그 레코드 용도부터 확인한다. apex/root·www는 변경하지 않는다.
6. SSH에서 공개 검사를 실행한다.

```bash
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/tunnel/check.py --scope public
```

이 검사는 고정 URL `https://agent.oreumit.com/readyz`만 호출한다. **무인증 거부 → Access 서비스 인증만 있을 때 앱 인증 거부 → Access 서비스 인증+올바른 HMAC으로 준비 응답 성공**을 확인한다. 리다이렉트를 따라가지 않고 HTTPS 인증서 검증을 유지한다. 응답 원문·비밀값은 출력하지 않는다. `scope:public`, `status:passed`, `modelExecuted:false`가 완료 기준이다. 공개 추천 실행이나 실제 Vercel 호출의 성공을 의미하지 않으며 `productionReady:false`를 유지한다.

다음은 Vercel **서버 환경**에 Gateway URL·HMAC 키와 Cloudflare Client ID/Secret을 비밀값으로 연결하고, 설문 입력 변환과 실제 요청 주체를 결합한 서버 어댑터를 Preview에서 검사하는 단계다. `NEXT_PUBLIC_*`나 브라우저 번들에 이 인증값을 넣지 않는다. 여기서는 Vercel 환경변수나 배포를 변경하지 않는다.

### 공개 검사의 1010과 단계별 실패 확인

2026-11-19 사용자 AWS 출력에서 `/healthz`·`/readyz`는 각각 17바이트의 403을 반환했다. Tunnel 로그의 route는 `http://agent-gateway:8790`, 빈 Path, 올바른 Host였고 추가 Tunnel Access 검증은 꺼져 있었으며 해당 JWT 거부 로그도 0건이었다. 작성자가 같은 공개 주소에 무인증 요청을 보내 Python 기본 User-Agent의 `error code: 1010`을 확인했다. `User-Agent: oreumit-agent-check/1.0`을 사용하자 1010이 사라졌다. [Cloudflare 공식 1010 안내](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)도 라이브러리 기본 User-Agent 대신 실제 앱 식별자를 보내도록 안내한다. 브라우저 식별자 사칭이나 보안 기능 비활성화가 필요하다는 근거는 없다.

검사기는 세 공개 요청 모두에 앱 식별자를 넣고, 실패 시 `TUNNEL_PUBLIC_CHECK_FAILED` JSON으로 `phase`, `httpStatus`, `bodyBytes`, `responseKind`와 확인 가능한 고정 오류 코드만 반환한다. 응답 원문·Cookie·JWT·Client ID/Secret·HMAC은 출력하지 않는다. `cloudflareCode`는 본문 전체가 알려진 짧은 오류 형식일 때만 숫자로 추출하고 `gatewayCode`는 Gateway 코드 허용 목록으로 제한한다.

| phase | 기대 결과 | 실패 시 확인할 범위 |
|---|---|---|
| `anonymous` | 무인증 거부(리다이렉트/401/403) | 공개 호스트 전체의 Access 보호, Cloudflare 오류 번호 |
| `access_only` | 앱 HMAC이 없으므로 401 | Access 서비스 인증과 Gateway의 헤더 거부 |
| `signed` | 두 인증을 통과한 200 | HMAC·Host·Access JWT·고정 흐름 준비 |
| `readiness_response` | `ready:true`, `mode:access`, 981곳 등 응답 계약 | 다른 서비스로 연결됐는지 또는 준비 상태 불일치 |

앱 식별자 추가 후 사용자 AWS 출력은 `anonymous:403`·61,599바이트와 `access_only:403`·36바이트·`ORIGIN_REJECTED`다. 이는 요청이 Gateway까지 도착한 근거이며 공개 검사 전체 성공은 아니다. `ORIGIN_REJECTED`에는 `gatewayHeaderContext`의 `originPresent`, `cookiePresent`, `cookieEmpty`, `singleCfAuthorizationCookie`, `cookieMatchesAssertion` boolean만 표시한다. 실제 사용자 진단은 `originPresent:false`, `cookiePresent:true`, `cookieEmpty:false`, `singleCfAuthorizationCookie:true`, `cookieMatchesAssertion:true`였다. Cloudflare의 서비스 인증을 거친 요청에 JWT 헤더와 같은 단일 `CF_Authorization` 쿠키가 도착했고, 기존 일괄 Cookie 거부가 이를 막았다.

Gateway는 Access 모드의 보호 경로에서만 JWT 헤더와 정확히 같은 단일 `CF_Authorization` 쿠키를 수용한다. 최초 경로는 `GET /readyz`·`POST /v1/recommend`이며 큐 적용 후에는 `POST /v1/jobs`·`GET/DELETE /v1/jobs/<jobId>`에도 같은 검증을 적용한다. 쿠키는 인증 근거로 사용하지 않으며 기존 앱 HMAC·공개 Host·JWT 서명/issuer/AUD/만료 검증을 모두 통과해야 한다. Origin 헤더, 일반·추가·중복·불일치 쿠키, 빈 쿠키, private 모드와 healthz의 쿠키는 계속 거부한다. Cloudflare 보안 기능이나 Access 정책을 끄지 않는다.

업데이트는 호스트 검사기와 Gateway의 한정된 쿠키 호환 처리·오류 진단을 바꾼다. 수정 브랜치를 적용한 뒤 3절의 `tunnel_compose` 배열을 정의하고 Gateway만 갱신한다. DB·MCP·Langflow·cloudflared 재설치는 필요하지 않다.

```bash
"${tunnel_compose[@]}" up -d --build --no-deps --wait --wait-timeout 90 agent-gateway &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 deploy/tunnel/check.py --scope public
```

`scope:public`, `status:passed`가 확인되기 전에는 Vercel 연결 완료나 운영 준비 완료로 기록하지 않는다.

### AWS 공개 인증 검사 성공

2026-11-19 사용자가 수정 커밋 `7890060`을 적용하고 Gateway만 다시 빌드한 뒤 아래 결과를 제공했다. 작성자가 AWS에 직접 SSH 접속한 결과는 아니다.

```json
{"status":"passed","scope":"public","tokenConfigured":true,"runtimeChecked":true,"modelExecuted":false,"productionReady":false}
```

이 검사는 무인증 거부, Access 서비스 인증만 있을 때 앱 HMAC 거부, 두 인증이 모두 있을 때 HTTP 200 및 Access 모드·고정 흐름·981곳 준비 응답을 확인한다. 실제 공개 추천 POST나 Vercel 서버 호출·웹 사용자 흐름을 검증한 결과는 아니다. 다음은 Vercel 서버 어댑터와 서버 비밀 설정·Preview 연결이며, `productionReady:false`를 유지한다.

## 6. 보존·중단·검증 범위

설정 helper는 같은 값이면 기존 파일을 보존하고, 다른 값·잘못된 소유권/권한·symlink·hardlink·일부만 남은 token 쌍을 거부한다. 실패했다고 파일을 지우거나 key를 자동 교체하지 않는다. 저장 중 실패하면 이번 호출에서 만든 링크만 되돌리고, 중단으로 partial pair가 남으면 일치하는 백업/현재 connector 설정부터 확인한다. 기존 DB·Langflow 암호나 암호화 키를 재생성할 이유는 없다.

공개 연결만 중단하려면 3절의 `tunnel_compose` 배열을 정의한 동일한 SSH 세션에서 아래를 사용한다. Gateway는 access 모드를 유지하고 다른 컨테이너·볼륨은 보존한다.

```bash
"${tunnel_compose[@]}" stop cloudflared
```

공식 이미지 확인일은 2026-11-19다. `cloudflare/cloudflared:2026.11.1`의 registry manifest-list digest는 **`sha256:e613aadbc2d9511785d2e67a86b0c8ac18dd63a3f9af0d06faf04cbfdcb35f2d`**이며 amd64/arm64를 제공한다. 실행 사용자 `65532:65532`, `--token-file`, 셸 없는 `cloudflared tunnel --metrics 127.0.0.1:2000 ready`를 공식 이미지 metadata와 릴리스 소스에서 확인했다. metrics는 컨테이너 loopback에만 바인딩한다.

로컬 단위 검사는 비밀값 보존, 권한·소유자·링크 거부, 토큰 쌍의 부분 실패, 잘못된 issuer/host/AUD, 서비스 Secret 형식과 공개 인증 응답 계약을 다룬다. Docker CI의 가상 카탈로그·모의 모델 검사는 실제 Cloudflare 계정·DNS·AWS·Vertex 실행 검증을 대신하지 않는다. 실제 계정 설정·Tunnel 연결·공개 인증 결과는 위 사용자 AWS 성공 기록과 구분해 읽는다.

```bash
python3 -m unittest discover -s deploy/tunnel -p 'test_*.py'
```

## 후보 표시 완화 적용

이 절은 **후보 복구만 적용하던 이전 절차**다. 요청 큐까지 포함한 현재 브랜치는 아래 [요청 큐 업데이트](#요청-큐와-동시-처리-업데이트)의 helper를 사용한다.

2026-11-19 사용자가 운영 웹에서 AI 결과가 차단됐다고 보고했다. 모델 응답 원문과 차단 사유를 확보하지 않았으므로 **그 요청의 정확한 원인은 아직 확정하지 않았다.** 아래 수정은 등록된 Gateway 요청의 후보 ID·조건 근거를 기준으로, 복구 가능한 구조화 응답을 좁게 정리한다. AWS 적용 및 수정 이후 실제 웹 추천 결과는 별도 확인이 필요하다.

`/gateway/validate-response`의 요청 범위 안에서만 모델이 이미 선택한 ID를 사용한다. 원래 필터·제외 목록 밖 ID와 확인된 필수 조건 실패 후보를 제거하고, 중복 제거 후 최대 5곳을 남긴다. 조건별 판정과 확인 안내는 카탈로그의 실제 근거로 다시 만들며 미검증 값은 `unknown`·`cannot_confirm`으로 유지한다. 새 장소를 대체 삽입하지 않고, 남길 후보가 없거나 엄격한 최종 검증을 통과하지 못하면 계속 차단한다. 요청 범위 밖의 기존 엄격 검증과 Langflow 그래프·프로토콜은 변경하지 않는다.

아래는 AWS SSH에서 실행할 적용 명령이다. 이미지 빌드 중에는 기존 MCP가 계속 실행된다. **웹 추천 요청이 없는 때 실행한다.** helper는 재시작 직전 기존 MCP의 인증된 `/gateway/readyz`를 확인하고 `activeScopes`가 0이 아니면 재시작 없이 중단한다. `ACTIVE_REQUEST_PRESENT`이면 현재 요청이 끝난 뒤 같은 helper를 직접 실행한다. 자동 재시도나 활성 요청 취소는 하지 않는다.

```bash
cd ~/oreumit-agent &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin refs/heads/codex/gateway-candidate-recovery:refs/remotes/origin/codex/gateway-candidate-recovery &&
git merge --ff-only origin/codex/gateway-candidate-recovery &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-candidate-recovery.sh
```

helper는 다섯 Compose 파일을 모두 사용하고 **`jeju-mcp`만** `--no-deps --wait`로 갱신한다. Gateway·Langflow·DB·Tunnel 컨테이너와 기존 자격증명·볼륨은 갱신하지 않는다. 다만 상태 확인과 재시작 사이 신규 요청을 원자적으로 막는 maintenance gate는 없으므로 무중단 적용을 보장하지 않는다. 이미지 갱신 중 새로 들어온 요청은 실패할 수 있다.

재기동 후에는 새 MCP 이미지와 현재 981곳 카탈로그를 별도 프로세스에서 불러와 합성 요청으로 후보 정리를 확인한다. 이 검사에 쓰는 scope는 **프로세스 메모리 안에만** 존재하며 실행 중 서비스에 등록하지 않는다. 이어 기존 공개 인증 준비 검사를 실행한다. 최종 성공 표시는 다음과 같으며 모델은 실행하지 않는다.

```json
{"status":"passed","scope":"candidate-recovery-applied","service":"jeju-mcp","modelExecuted":false,"productionReady":false}
```

## 요청 큐와 동시 처리 업데이트

사용자 요청으로 PR #19·`codex/gateway-candidate-recovery`에 후보 복구와 함께 요청 큐를 추가했고 main에 병합했다. 기본값은 **전체 실행 2건·대기 최대 8건·사용자별 실행과 대기 합계 1건**이다. AWS 적용과 실제 합성 요청 2건의 결과는 아래 실측 기록을 따른다. AWS 메모리 8GB와 단발 검사만으로 지속 처리량이나 지연을 보장하지 않는다. 기존 Langflow·Vertex 공급자 설정을 유지한다.

| Compose 설정 | 기본값 | 허용 범위 |
|---|---:|---|
| `GATEWAY_MAX_CONCURRENT` | 2 | 정수 1~4, Gateway와 MCP가 같은 값을 사용 |
| `GATEWAY_MAX_QUEUE` | 8 | 정수 0~8, 0이면 대기 접수 안 함 |
| `GATEWAY_GLOBAL_HOURLY_LIMIT` | 60 | 시간당 전체 1~240건 |
| `GATEWAY_SUBJECT_HOURLY_LIMIT` | 12 | 시간당 사용자 1~48건, 전체 한도 이하 |

위 값은 Compose를 실행하는 환경 또는 `deploy/.env`에서 지정하는 **비밀이 아닌 설정**이다. 두 서비스의 동시 처리 값을 따로 바꾸지 않는다. `deploy/.local/gateway.env`에만 적으면 Compose의 `environment` 기본값이 우선하므로 적용되지 않는다. 기본값을 쓸 때 새 파일이나 키를 만들 필요가 없다. 기존 시간당 사용량·재전송 방지 기록을 지워 한도를 초기화하지 않는다.

### AWS에 적용할 명령

웹에서 새 추천을 제출하지 않는 때 AWS SSH에서 실행한다. 두 이미지를 모두 빌드한 뒤 **인증된 Gateway 준비 응답의 queued/running/uncertain가 모두 0이고 MCP activeScopes가 0인지** 확인한다. 큐가 없는 구버전은 `/app/jobs.mjs` 부재와 SQLite `user_version=1`을 확인한 경우에만 기존 DB를 읽기 전용으로 열어 살아 있는 inflight/uncertain lease 수를 확인한다. DB가 없거나 스키마·읽기·인증 검사에 실패하면 재시작 전에 중단한다. 새 상태 저장소를 만들거나 마이그레이션해서 유휴 상태를 추정하지 않는다.

```bash
cd ~/oreumit-agent &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin refs/heads/codex/gateway-candidate-recovery:refs/remotes/origin/codex/gateway-candidate-recovery &&
git merge --ff-only origin/codex/gateway-candidate-recovery &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-queue.sh
```

`ACTIVE_REQUEST_PRESENT` 또는 종료 코드 75는 요청·대기 작업·불확실 실행 보호 시간이 남았다는 뜻이다. 모두 끝난 뒤 같은 helper를 직접 다시 실행한다. 다른 실패도 원인을 확인한 뒤 재개하며 자동 반복하지 않는다. 상태 확인과 재기동 사이 신규 요청을 원자적으로 막는 maintenance gate는 없으므로 무중단 적용을 보장하지 않는다. 이 사이 새 요청이 접수되면 영향을 받을 수 있다.

helper는 Compose 파일 5개를 모두 사용해 **`jeju-mcp`와 `agent-gateway`만** `--no-deps --wait`로 갱신한다. Langflow·DB·cloudflared를 재생성하지 않고 자격증명·SQLite·볼륨을 삭제하거나 작업을 취소하지 않는다. MCP 후보 복구의 오프라인 검사, 새 Gateway 이미지의 임시 SQLite·모의 작업 기반 큐/상태 검사, 기존 공개 인증 준비 검사 순으로 확인한다. 테스트 파일은 이미지에서 제외하므로 호스트의 파일 두 개를 각각 stdin으로 전달해 `/app`의 설치된 모듈을 검증한다. 마지막으로 인증된 준비 응답의 `asyncJobs:true`와 Gateway `maxConcurrent`·MCP `maxConcurrentScopes` 일치를 확인한다. 이 준비 검사는 새 사용자의 활성 작업을 취소하지 않는다.

이미 컨테이너 갱신과 후보 오프라인 검사가 통과했으나 `queue_offline_check`에서 `Could not find 'state.test.mjs, jobs.test.mjs'`로 멈췄다면 이전 검사 스크립트의 이미지 파일 경로 오류다. 수정본을 받은 뒤 아래 명령으로 검사만 재개한다. 빌드·유휴 검사·서비스 재시작은 하지 않는다. 성공 출력은 `scope:agent-queue-verified`, `servicesRestarted:false`다.

```bash
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-queue.sh --verify-only
```

최종 완료 출력은 다음과 같다. **실제 모델을 호출하거나 Langflow bootstrap·기존 성공한 단건 모델 검사를 반복하지 않는다.**

```json
{"status":"passed","scope":"agent-queue-applied","services":["jeju-mcp","agent-gateway"],"modelExecuted":false,"productionReady":false}
```

### 대기·실패·재시작의 의미

- 서명된 `POST /v1/jobs`는 요청을 한 번 접수한다. 같은 사용자만 서명된 `GET /v1/jobs/<jobId>`로 상태·결과를 조회하며 조회는 모델 실행·시간당 한도를 추가 소비하지 않는다. 기존 동기식 `/v1/recommend`도 호환한다.
- 실행 슬롯이 비면 대기열의 다음 작업이 실행된다. 대기 순서는 예상 완료 시간이 아니다. 대기 만료는 10분, 전체 작업 수명은 최대 12분이며 실행 예산은 실제 작업이 시작할 때 적용한다.
- 접수 전의 `BUSY`·`QUEUE_FULL`·`RATE_LIMITED`와 접수 후 `queued`·`running`을 구분한다. 웹은 먼저 기존 `/readyz`의 `asyncJobs:true`를 확인한 뒤 큐를 사용하고, 이 기능 표시가 없는 구버전은 기존 동기 경로를 사용한다. 큐 기능을 확인한 뒤에도 경로가 명확한 404 `NOT_FOUND`를 반환하는 경우에만 동기 경로를 한 번 사용한다. 다른 오류나 사라진 개별 작업은 자동 재제출하지 않는다.
- Langflow 종료 응답이 확인된 뒤 최종 검증에서 실패한 작업은 슬롯을 해제한다. 시간 초과·중단·전송 실패처럼 종료 여부를 확인하지 못한 작업은 기존 120초 보호 lease를 유지한다. 실패를 완료로 임의 처리하거나 곧바로 반복 실행하지 않는다.
- 대기 요청·결과 원문은 RAM에만 있고 완료 결과는 최대 5분·128건으로 제한한다. 재시작 후 대기 작업을 복원·자동 실행하지 않는다. SQLite에는 해시와 사용량·재전송·lease 상태만 남기며, 재시작 전 실행 결과가 불확실하면 그 보호 시간을 유지한다. 사라진 작업의 404는 재전송 승인이 아니다.
- 취소 API는 **아직 대기 중인 작업만** 취소한다. 이미 실행 중인 모델은 취소 API로 중단하거나 재실행하지 않는다. 적용 helper는 취소 API를 호출하지 않는다.

AWS 적용 출력과 이후 실제 사용자 응답을 따로 확인한다. 오프라인 검사·공개 준비 검사 통과만으로 병렬 모델 성공·운영 품질 완료를 기록하지 않고 `productionReady:false`를 유지한다.

### 선택: AWS에서 실제 모델 요청 2건 확인

위 적용이 성공한 뒤 기본 동시 처리 2건 설정·유휴 상태에서만 실행한다. **합성 추천 요청 2건을 실제 Langflow/Vertex로 전송하므로 API 사용료가 발생할 수 있다.** 요청 하나 안에서 모델이 여러 차례 호출될 수 있으므로 모델 API를 정확히 두 번 부른다는 뜻은 아니다. 기존 성공한 단건 검사를 반복하는 절차가 아니며 적용 helper가 자동으로 실행하지 않는다.

```bash
cd ~/oreumit-agent &&
sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap python3 -I deploy/check-agent-queue-model.py --execute-model
```

검사기는 기존 AWS 비밀 파일을 내부에서 읽어 서명하며 키를 새로 만들거나 채팅 입력을 요구하지 않는다. 서로 다른 합성 주체의 `/v1/jobs` 제출을 각각 한 번만 하고 이후에는 상태 GET만 조회한다. 요청 전송 결과가 불확실해도 POST를 다시 보내지 않는다. 마지막의 정제된 JSON 집계만 공유하며 비밀 파일·응답 원문·컨테이너 환경 전체를 출력하지 않는다.

`scope:aws-two-job-model-check`, `recommendationPostAttempts:2`, `acceptedResults:2`, `twoRunningObserved:true`이면 두 작업의 Gateway 실행 상태와 최종 응답 수락을 관측한 것이다. 경과 시간과 메모리 표본을 함께 남기지만 비용·최대 처리량·모델 공급자 내부의 정확한 병렬 구간은 측정하지 않는다(`providerModelSpanOverlapVerified:false`). 두 결과가 수락돼도 두 running 상태를 관측하지 못하면 `inconclusive`다. 실패·불확실 결과가 나오면 같은 명령을 반복하기 전에 집계와 기존 작업 상태를 확인한다. 이 문서 작성 환경에서는 실제 요청을 실행하지 않았다.

실패하면 표시된 단계와 고정 오류 코드만 공유한다. 환경 파일·키·서비스 응답 원문을 출력하거나 새로 발급할 필요가 없다. 적용 성공은 소스·카탈로그·공개 준비 확인이며 실제 추천 품질이나 운영 준비 완료를 뜻하지 않는다. 이후 웹에서 사용자가 직접 요청한 실제 결과를 확인하되, 이 확인을 위해 기존 모델 평가를 반복하지 않는다.

### AWS 실제 추천 2건 검사 결과

2026-11-19(KST) 사용자가 AWS SSH에서 실행한 출력이다. 작성자의 직접 원격 실행과 구분한다. 첫 적용은 서비스 갱신과 981곳 후보 오프라인 검사 후 이미지에서 제외된 `*.test.mjs`를 찾지 못해 중단됐다. 호스트 테스트를 stdin으로 전달해 재시작 없이 남은 검사를 마쳤고 `scope:public`·`scope:queue-runtime-ready` 모두 통과했다. 이때 `maxConcurrent:2`, queued/running/uncertain 및 MCP activeScopes는 모두 0이었다. 검사 스크립트의 영구 수정은 PR #20이다.

그 후 `check-agent-queue-model.py --execute-model`을 한 번 실행한 결과:

| 항목 | 사용자 제공 출력 |
|---|---|
| 검사 상태 | `passed`, `aws-two-job-model-check` |
| 추천 제출·수락 | POST 2건·최종 수락 2건, 자동 재제출 없음 |
| 실행 관측 | `twoRunningObserved:true`, 상태 조회 15회 |
| 결과별 경과 시간 | 16.79초·14.71초, 전체 16.79초 |
| 후보·최종 검증 | 각 3곳, 두 결과 모두 `finalValidationAccepted:true` |
| 조건 안내 | 둘 다 `cannot_confirm`, `confirmationNotice:true`; 미검증 실내 조건의 정상 안내 |
| 실행 중 메모리 표본 | Gateway 36.54/256 MiB, MCP 34.80/512 MiB, Langflow 1376.26/3072 MiB |
| 종료 후 메모리 표본 | Gateway 36.05 MiB, MCP 35.08 MiB, Langflow 1380.35 MiB |

두 Gateway 작업의 running 상태를 같은 확인 주기에서 관측했고 최종 응답이 모두 수락됐다. `providerModelSpanOverlapVerified:false`이므로 공급자 내부 모델 실행 구간의 정확한 겹침은 확인하지 않았다. 추천 2건은 모델 API 호출 총 2회라는 뜻이 아니다. 메모리 값은 전·중·후 표본이며 최고 사용량·지속 부하·요금은 측정하지 않았다. 이 실측은 AWS Gateway→Langflow/Vertex 경로이며 로그인 웹 사용자의 화면 확인과 구분한다. `productionReady:false`를 유지하고 성공한 검사를 반복하지 않는다.

## 모델 호출 예산 조정

2026-11-21 구현·로컬·CI 검증과 사용자 AWS 적용 기록이다. **AWS 적용은 [사용자 제공 출력](#aws-적용-결과사용자-제공-출력)으로 성공했고, 실제 웹 추천의 차단 감소는 아직 확인하지 않았다.**

### 원인 진단(사용자 제공 운영 집계와 추정)

사용자가 운영 웹에서 "조건과 장소를 확인할 수 없어 AI 결과를 표시하지 않았어요"가 자주 표시된다고 보고했다. 2026-11-21 사용자가 AWS Langflow DB에서 읽기 전용 SQL로 Gateway 흐름(`gw-` 세션)의 실행 기록을 집계해 제공했다. 대상 실행은 모두 DB(UTC) 날짜 2026-11-19분이다. 작성자의 직접 원격 실행 결과가 아니다.

| 최종 결과 | 건수 | 실행당 모델 호출 | 평균 도구 호출 |
|---|---:|---:|---:|
| 최종 검증 수락 | 2 | 4회 | 3회 |
| `VALIDATION_FAILED` | 13 | 4회 | 4회 |

마지막 모델 출력에는 코드 블록·`impossible`·`ask_clarification`이 없었다(세션 16개, Agent 오류 1건). 수락 2건도 4회를 모두 썼고 4번째 호출에서 최종 JSON을 냈다. 실패 13건은 4회를 모두 도구 호출에 쓴 것으로 보인다. 그러면 한도에 이른 LangChain `ModelCallLimitMiddleware`가 `Model call limits exceeded: run limit (4/4)` 문장을 마지막 응답으로 남긴다. 최종 검증 컴포넌트가 이를 JSON으로 읽지 못해 `VALIDATION_FAILED`로 차단하고, Gateway가 HTTP 422로 바꾸며, 웹이 위 문구를 표시한다.

이 원인은 집계와 코드 경로에 근거한 **추정**이다. 당시 SQL은 한도 문장 자체나 마지막 출력의 도구 호출 여부를 세지 않았다. 이 모델은 한 응답에서 여러 도구를 함께 부르기도 하므로([28 문서](28-agent-v2-evaluation.md)) 평균만으로 실행별 원인을 확정할 수 없다. 아래 [확인 SQL](#적용-전후-확인-sql)의 `limit_notice` 열로 직접 확인한다.

### 변경 범위

- **Gateway 흐름에만** 적용한다. 측정된 v2 기준선 흐름·템플릿·기본 bootstrap과 MCP 최종 검증 규칙은 바꾸지 않는다.
- 모델 호출 상한을 4회에서 **8회**로 올린다. MCP의 요청당 도구 호출 상한 8회에 맞춘 값이며 두 상한은 따로 센다.
- 마지막 허용 호출(8번째)은 **답변 전용**이다. 대화 기록이 유효하도록 도구 선언은 유지하고 `tool_choice="none"`(Gemini `function_calling_config.mode=NONE`)으로 새 도구 호출을 막는다. 시스템 지침 끝에는 "마지막 호출이므로 지금까지의 도구 결과로 위 형식의 JSON 객체 하나만 출력한다"는 안내를 붙인다. 판정 규칙은 바꾸지 않으며 응답은 기존처럼 MCP 최종 검증을 다시 통과해야 한다.
- 출력 2048토큰·90초 제한·응답 형식·요청 범위 도구는 그대로다. 요청당 모델 호출이 최대 2배가 될 수 있어 지연과 비용이 늘 수 있다(미측정).
- 4번째 호출 안에 끝나던 실행은 처음 4회의 요청이 같으므로 이전과 같게 동작한다. 늘어난 5~8번째 호출은 원래 차단되던 실행에만 쓰인다.

### 검증한 것과 하지 않은 것

- 오프라인 단위 테스트: 흐름 변환(Agent 코드·상한·설명만 변경), 4↔8 교체·되돌리기·멱등성, 알 수 없는 그래프와 동시 편집 거부, 저장 결과가 다를 때 이전 그래프 복원, 보호 미들웨어 판정, 중단된 교체의 재개, 적용 helper의 순서와 실패별 안내.
- 로컬 실제 런타임(모델 공급자 호출 없음): Langflow의 lfx 1.13.3 로더로 실제 Agent 코드를 컴파일하고, 항상 도구를 요청하는 가짜 모델을 LangChain 1.3.18 에이전트 루프에 연결했다. Langflow가 계산하는 재귀 한도에서 4회 흐름은 한도 문장으로 끝났다. 운영 실패의 추정 경로와 같은 형태다. 8회 흐름은 도구 호출 7회 뒤 8번째 호출에서 도구 없이 최종 JSON을 반환했다(동기·비동기 모두). langchain-google-genai 4.1.3 요청 생성기에서 `tool_choice="none"`이 `function_calling_config.mode=NONE`이 되고 도구 선언이 유지됨을 오프라인으로 확인했다.
- CI(PR #21): `agent-gateway-docker.yml`이 공식 Langflow 1.13.3 컨테이너에서 저장된 흐름을 8→4→8회로 바꿨다. 매번 Gateway 흐름 고정 검사(`flowVerified:true`)와 저장된 Agent 코드의 가짜 모델 실행 검사를 통과했다. 허구 카탈로그이며 Google 추론은 없다.
- 적용 전 검토(작성 측 검토 에이전트, 두 차례): 1차에서 흐름 교체 뒤 Gateway 재고정 전에 중단되면 재실행으로 복구할 수 없는 문제를 찾아 진행 표시와 재개 경로를 넣었다. 2차에서 재개 확인이 MCP만 보면 대기·불확실 작업을 놓칠 수 있음을 찾아 Gateway 내부 상태 DB 확인을 더했다. 가짜 명령으로 만든 중단 지점 조합 시뮬레이션에서 유휴 상태의 1회·2회 중단이 모두 같은 최종 상태로 수렴했다. 실제 Docker 동작은 시뮬레이션하지 않았다.
- 하지 않은 것: 실제 Gemini 응답에서 차단이 얼마나 줄어드는지, 지연·비용, 사용자 AWS 적용, 로그인 웹 사용자의 결과 표시. 실제 Vertex에서 8번째 호출 형식(이전 함수 호출 기록 + `mode=NONE`)이 그대로 받아들여지는지도 아직 실측하지 않았다.

알려진 한계:

- 8회 안에서도 최종 JSON이 형식이나 검증 규칙을 어기면 계속 차단된다.
- 최종 검증 컴포넌트는 Agent 메시지 전체 텍스트를 읽는다. 도구를 부르는 응답에 모델이 설명 문장을 붙이면 그 문장이 최종 JSON 앞에 합쳐져 차단된다. 이 변경 전부터 있던 경로이며 호출이 늘면 기회도 늘어난다.
- 5~8번째 호출의 공급자 오류·시간 초과는 재시도 없이 Agent 오류가 된다. Gateway는 이를 502와 120초 이상의 불확실 실행 보호로 처리한다. 원래 422로 바로 끝나던 실행이므로 성공을 잃지는 않지만 실행 슬롯과 같은 사용자의 재요청이 잠시 묶일 수 있다.
- 쓸 수 있는 장소 ID 없이 8번째 호출에 이르면 장소 없는 `ask_clarification`만 검증을 통과한다. 이는 웹에서 차단 문구 대신 추가 확인 안내로 보이지만 추천 성공은 아니다.

### AWS에 적용할 명령

웹에서 새 추천을 제출하지 않는 때 AWS SSH에서 **한 줄씩** 실행한다. 모델을 호출하지 않고 API 키·흐름 ID·토큰을 바꾸지 않는다. Langflow·MCP·DB·cloudflared 컨테이너는 재시작하지 않는다. 각 명령은 `cd ~/oreumit-agent`로 시작하므로 SSH에 다시 접속한 직후에도 그대로 붙여 넣으면 된다.

```bash
cd ~/oreumit-agent && git diff --quiet && git diff --cached --quiet && git fetch origin refs/heads/claude/agent-step3-evaluation:refs/remotes/origin/claude/agent-step3-evaluation && git merge --ff-only origin/claude/agent-step3-evaluation
```

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-call-budget.sh
```

helper는 Compose 설정 확인 → 이전 교체 미완료 여부 확인 → 유휴 확인 → `prepare-gateway.py --update-flow` → `agent-gateway`만 재생성 → `--check-flow` → 공개 인증 준비 검사 → 큐 준비 검사 → 미완료 표시 해제 순서로 진행한다. 유휴 확인은 기존 큐 적용과 같다. queued/running/uncertain 또는 MCP activeScopes가 남아 있으면 종료 코드 75로 멈추며 아무것도 바꾸지 않는다. 모두 끝난 뒤 직접 다시 실행한다.

`--update-flow`는 설치 스크립트를 Langflow 컨테이너의 단일 파일 바인드 마운트(컨테이너 생성 시점 내용) 대신 stdin으로 보낸다. 저장된 Gateway 흐름이 **검토된 4회 빌드와 정확히 같을 때만** `If-Match` PATCH로 그래프와 설명만 8회 빌드로 바꾸고, 다시 읽어 일치를 확인한다. 이미 8회 빌드면 바꾸지 않는다. 그 밖의 그래프는 `GATEWAY_EXISTING_FLOW_DIFFERS`로 거부한다. 이어 `deploy/.local/gateway.env`의 `LANGFLOW_GATEWAY_FLOW_SHA256` 한 값만 같은 잠금 아래 원자적으로 바꾼다. Langflow는 PATCH 때 `updated_at`을 갱신하고 재사용 그래프 캐시를 이 값으로 구분하므로 이전 그래프를 계속 쓰지 않는다. 같은 이유로 `--provision`도 이제 stdin으로 같은 설치 스크립트를 보낸다.

성공 출력:

```json
{"status":"passed","scope":"agent-call-budget-applied","modelCallLimit":8,"services":["agent-gateway"],"modelExecuted":false,"productionReady":false}
```

### AWS 적용 결과(사용자 제공 출력)

2026-11-21 사용자가 AWS SSH에서 `main@ce2f657`로 빨리 감은 뒤 적용 helper를 한 번 실행한 출력이다. 작성자의 직접 원격 실행 결과가 아니다.

| 단계 | 사용자 제공 출력 |
|---|---|
| 미완료 교체 확인 | `flowChangePending:false` |
| 유휴 확인 | queued·running·uncertain 0, MCP activeScopes 0, `maxConcurrent:2` |
| 흐름 교체 | `flowStatus:updated`, `settingsChanged:true`, `modelCallLimit:8` |
| Gateway 재생성 | `agent-gateway`만 재생성, 16.3초에 Healthy |
| 흐름 고정 확인 | `gateway-flow-checked` |
| 공개 인증 준비 검사 | `scope:public`, `runtimeChecked:true` |
| 큐 준비 검사 | `queue-runtime-ready`, 작업 0, `maxConcurrent:2` |
| 마무리 | `markerCleared:true` → `agent-call-budget-applied` |

`flowStatus:updated`는 AWS에 저장돼 있던 Gateway 흐름이 검토된 4회 빌드와 정확히 같았고 같은 흐름 ID에서 8회 빌드로 바뀌었다는 뜻이다. API 키·흐름 ID·토큰은 바뀌지 않았고 Langflow·MCP·DB·cloudflared는 재시작하지 않았다. 이 적용은 모델을 실행하지 않았다(`modelExecuted:false`).

적용 뒤 사용자가 공유한 운영 웹 화면 1건에서는 차단 문구 대신 AI 후보 5곳(전시·역사 2곳, 카페 3곳)이 표시됐다. 단건 관측이며 그 요청의 모델 호출 수·차단 비율은 확인하지 않았다.

아직 확인하지 않은 것: 실제 Vertex에서 8번째 호출 형식(이전 함수 호출 기록 + `mode=NONE`)이 받아들여지는지, 실제 웹 요청에서 차단이 줄어드는지, 지연·비용. 다음은 실제 웹 요청이 쌓인 뒤 아래 [확인 SQL](#적용-전후-확인-sql)로 적용 전후를 비교하는 것이다. `productionReady:false`를 유지한다.

### 중단됐을 때

흐름을 바꾼 뒤 Gateway를 재생성하기 전까지 Gateway는 `FLOW_CHANGED`(503)로 새 요청을 거부하며 고정되지 않은 그래프를 실행하지 않는다. 이 사이에 브라우저 SSH가 끊기거나 명령이 실패할 수 있다. 이때는 **위 적용 명령을 다시 실행한다**(되돌리려면 아래 `--rollback`).

- 흐름 교체를 시작하기 전에 `deploy/.local/.gateway-flow-change` 표시 파일을 만든다. Gateway 재고정과 모든 준비 검사를 통과한 뒤에만 지운다. 교체 스크립트가 PATCH를 보내기 전에 거부(`GATEWAY_EXISTING_FLOW_DIFFERS` 등)로 끝나 아무것도 바뀌지 않았으면, 그 실행이 만든 표시는 바로 지운다.
- 다시 실행했을 때 공개 준비 확인이 `FLOW_CHANGED`로 실패하거나, 표시 파일이 남아 있는데 공개 확인이 실패하면 **로컬 재개 확인**으로 넘어간다. Gateway 컨테이너 안의 상태 DB를 읽기 전용으로 열어 대기·실행·불확실 작업 수를 세고, MCP의 실행 중 요청 범위도 확인한다. 하나라도 남아 있으면 종료 코드 75로 멈추고 아무것도 바꾸지 않는다. 모두 0이면 흐름 고정과 Gateway 재생성을 이어서 마친다. Gateway 컨테이너가 멈춰 있으면 MCP만 확인한다.
- 표시 파일이 없고 공개 확인이 `FLOW_CHANGED`가 아닌 이유로 실패하면, 이번 실행은 아무것도 바꾸지 않고 멈춘다.
- 실패 안내에 `흐름 변경이 끝나지 않았습니다`가 나오면 위와 같이 다시 실행한다. 표시 파일이 남아 있는 동안 `--verify-only`는 마무리를 요구하며 실패한다.
- 적용이 중단된 뒤 `--rollback`으로 재개하면(또는 그 반대), PATCH와 Gateway 재생성 사이 1~3초 동안 이전 Gateway가 다시 요청을 받을 수 있다. 이 경우에도 반드시 웹 제출을 멈춘 상태에서 실행한다.

### 확인과 되돌리기

재시작 없이 다시 확인하는 명령이다. 8회 흐름 상태만 확인한다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-call-budget.sh --verify-only
```

기존 4회 흐름으로 되돌리는 명령이다. 같은 유휴 확인 뒤 4회 빌드를 고정하고, 그 실행 안에서 저장 그래프 재조회와 Gateway 준비 검사로 확인한다. 되돌린 뒤의 `--verify-only`는 8회 기준이라 실패하므로 쓰지 않는다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-call-budget.sh --rollback
```

### 적용 전후 확인 SQL

Langflow DB를 읽기만 하는 명령이다. 모델이나 서비스를 호출하지 않고 응답 원문·비밀값을 출력하지 않는다. 시각과 기간 경계는 DB 기준인 UTC(+00)다. KST는 9시간을 더한다.

- 기간은 [1]에 나오는 **가장 최근 흐름 변경**(적용·되돌리기·재적용 모두 포함) 전후로 나뉜다.
- 적용 전에 실행하면 Gateway 설치 이후의 실행이 모두 `after_flow_change`로 나오며, 이때 `limit_notice`로 위 추정을 확인할 수 있다.
- 적용하면 그 전 실행은 `before_flow_change`, 새 실행은 `after_flow_change`가 된다.
- 되돌리거나 다시 적용한 뒤에는 경계가 그 시각으로 옮겨진다. 8회 흐름의 실행은 `model_calls`가 5 이상인 행으로 구분한다.

```bash
cd ~/oreumit-agent && sudo docker compose -p oreumit-bootstrap -f deploy/compose.yaml -f deploy/compose.catalog.yaml -f deploy/compose.langflow.yaml exec -T langflow-db sh -eu -c 'PGPASSWORD="$LANGFLOW_DB_PASSWORD" psql -h 127.0.0.1 -U langflow_app -d langflow -P pager=off -v ON_ERROR_STOP=1' <<'SQL'
\echo '[1] last Gateway flow change (the call-budget PATCH updates it)'
SELECT max(updated_at) AS flow_changed_at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행';
\echo '[2] runs by period, final outcome, action and model calls'
WITH changed AS (SELECT max(updated_at) AS at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행'), final AS (SELECT m.session_id, CASE WHEN m."timestamp" > (SELECT at FROM changed) THEN 'after_flow_change' ELSE 'before_flow_change' END AS period, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN substring(m."text" from '"code":"([A-Z_]+)"') ELSE 'ACCEPTED' END AS outcome, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN NULL ELSE m."text"::jsonb ->> 'action' END AS action, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN NULL ELSE jsonb_array_length(m."text"::jsonb -> 'placeIds') END AS places FROM message m WHERE m.session_id LIKE 'gw-%' AND m.sender = 'Machine' AND m.properties::jsonb -> 'source' ->> 'id' = 'JejuFinalValidation-v2'), spans AS (SELECT t.session_id, s.span_type, coalesce(s.inputs::text, '') || coalesce(s.outputs::text, '') AS body FROM trace t JOIN span s ON s.trace_id = t.id WHERE t.session_id LIKE 'gw-%'), calls AS (SELECT session_id, count(*) FILTER (WHERE span_type = 'llm') AS llm, count(*) FILTER (WHERE span_type = 'tool') AS tool, bool_or(body LIKE '%Model call limits exceeded%') AS limit_notice FROM spans GROUP BY 1) SELECT f.period, f.outcome, coalesce(f.action, '-') AS action, coalesce(c.llm, 0) AS model_calls, count(*) AS runs, round(avg(coalesce(c.tool, 0)), 1) AS avg_tool_calls, count(*) FILTER (WHERE c.limit_notice) AS limit_notice, count(*) FILTER (WHERE f.places > 0) AS with_places FROM final f LEFT JOIN calls c USING (session_id) GROUP BY 1, 2, 3, 4 ORDER BY 1 DESC, 2, 3, 4;
\echo '[3] sessions and agent errors by period'
WITH changed AS (SELECT max(updated_at) AS at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행') SELECT CASE WHEN m."timestamp" > (SELECT at FROM changed) THEN 'after_flow_change' ELSE 'before_flow_change' END AS period, count(DISTINCT m.session_id) AS sessions, count(DISTINCT m.session_id) FILTER (WHERE m.category = 'error') AS with_agent_error, round(100.0 * count(DISTINCT m.session_id) FILTER (WHERE m.category = 'error') / count(DISTINCT m.session_id), 1) AS agent_error_pct FROM message m WHERE m.session_id LIKE 'gw-%' GROUP BY 1 ORDER BY 1 DESC;
\echo '[4] shares by period (blocked = every non-ACCEPTED outcome)'
WITH changed AS (SELECT max(updated_at) AS at FROM flow WHERE name = '제주 관광 Agent v2 — Gateway 제한 실행'), final AS (SELECT m.session_id, CASE WHEN m."timestamp" > (SELECT at FROM changed) THEN 'after_flow_change' ELSE 'before_flow_change' END AS period, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN substring(m."text" from '"code":"([A-Z_]+)"') ELSE 'ACCEPTED' END AS outcome, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN NULL ELSE m."text"::jsonb ->> 'action' END AS action, CASE WHEN m."text" LIKE '{"status":"blocked"%' THEN NULL ELSE jsonb_array_length(m."text"::jsonb -> 'placeIds') END AS places FROM message m WHERE m.session_id LIKE 'gw-%' AND m.sender = 'Machine' AND m.properties::jsonb -> 'source' ->> 'id' = 'JejuFinalValidation-v2'), spans AS (SELECT t.session_id, s.span_type, coalesce(s.inputs::text, '') || coalesce(s.outputs::text, '') AS body FROM trace t JOIN span s ON s.trace_id = t.id WHERE t.session_id LIKE 'gw-%'), calls AS (SELECT session_id, count(*) FILTER (WHERE span_type = 'llm') AS llm, count(*) FILTER (WHERE span_type = 'tool') AS tool, bool_or(body LIKE '%Model call limits exceeded%') AS limit_notice FROM spans GROUP BY 1) SELECT f.period, count(*) AS runs, count(*) FILTER (WHERE f.outcome <> 'ACCEPTED') AS blocked, round(100.0 * count(*) FILTER (WHERE f.outcome <> 'ACCEPTED') / count(*), 1) AS blocked_pct, count(*) FILTER (WHERE f.places > 0) AS with_places, round(100.0 * count(*) FILTER (WHERE f.places > 0) / count(*), 1) AS with_places_pct, count(*) FILTER (WHERE f.outcome = 'ACCEPTED' AND coalesce(f.places, 0) = 0) AS accepted_without_places, count(*) FILTER (WHERE c.limit_notice) AS limit_notice FROM final f LEFT JOIN calls c USING (session_id) GROUP BY 1 ORDER BY 1 DESC;
SQL
```

결과는 집계 표만 공유한다. 열의 뜻은 다음과 같다.

- `limit_notice`: 그 실행의 추적 기록에 한도 문장이 있는 실행 수다. Langflow 1.13.3은 검증 컴포넌트의 입력(Agent 응답)을 추적에 남긴다. 그러므로 `model_calls`가 1 이상인 행에서 `limit_notice`가 `runs`보다 작으면, 그만큼은 한도 문장으로 끝난 실행이 아니며 그 실행에 대한 위 추정은 맞지 않는다. 추적이 없는 `model_calls` 0인 행만 판단할 수 없다.
- `with_places`: 수락 결과 중 장소가 1곳 이상인 실행 수다. `accepted_without_places`는 장소 없는 수락(예: `ask_clarification`)이며 차단 감소로 세지 않는다.
- `blocked`: `ACCEPTED`가 아닌 모든 결과(`VALIDATION_FAILED`, `RECOMMENDATION_BLOCKED`)다. 웹에서는 모두 같은 차단 문구로 보인다.
- `model_calls`가 5 이상인 행은 8회 흐름의 실행이다. 4 이하인 실행은 어느 흐름이었는지 이 열만으로 구분하지 않는다.

되돌리기를 검토할 기준이다. 건수가 아니라 [3]·[4]의 **비율**로 비교하며, 적용 후 실행이 적어도 20건 쌓인 뒤 판단한다.

- `after_flow_change`의 `agent_error_pct`가 이전보다 뚜렷이 높거나, 웹에서 502·잠시 후 재시도 안내가 늘어난다.
- `blocked_pct`가 줄지 않거나 `with_places_pct`가 늘지 않는다. 이때 `model_calls`가 8인 실행이 주로 차단으로 끝나면 설명 문장 결합 같은 다른 원인을 조사한다.
- 응답 지연이나 비용이 받아들일 수 없게 늘어난다. 이 SQL로는 측정하지 않는다.

적용 성공은 흐름 교체와 준비 검사 통과이며 추천 품질 개선이나 운영 준비 완료를 뜻하지 않는다. `productionReady:false`를 유지한다.

## 규칙 동작 흐름과 gpt-5.4-mini 전환

2026-11-22 구현·로컬 검증 기록이다. **AWS 적용과 실제 gpt-5.4-mini 호출은 아직 하지 않았다.**

### 왜 바꾸는가

로컬 Qwen 실험([33 문서 2.1절](33-huggingface-models-evaluation.md#21-고도화-실험-동작은-서버-규칙-모델은-장소-선택만-2026-11-22))에서 모델이 동작까지 정하면 차단이 26/96이었다. 서버 규칙이 동작을 정하고 모델은 장소만 고르게 하자 차단이 0/96이 됐다. 같은 결과는 새 96문항에서도 유지됐다. 운영 차단도 대부분 최종 JSON 형식 문제였다(위 [원인 진단](#원인-진단사용자-제공-운영-집계와-추정)).

사용자 선택(2026-11-22)은 다음과 같다.
- 흐름 전체를 교체한다.
- 후보 검색은 지금처럼 모델이 도구로 하고, 판단은 서버가 한다.
- 모델은 Vercel 앱의 여행 도우미와 같은 **gpt-5.4-mini**를 쓴다.
- 모델 호출 위치는 **AWS Langflow**다.

### 변경 범위

- **모델의 출력**은 `{"placeIds":["…", …]}` 하나뿐이다.
  - 모델은 지금처럼 `places.search`·`places.get` 도구로 장소를 찾는다. 도구는 원래 요청 범위로 제한되고 요청당 8회까지다.
  - 동작·조건 판정·확인 안내는 모델이 쓰지 않는다.
- **MCP의 응답 조립**(`/gateway/validate-response`):
  - 위 모양의 응답을 받으면 등록된 요청 범위 안의 ID만 남긴다.
  - 확인된 조건 실패 장소, 중복, 범위 밖 ID를 버리고 모델 순서대로 최대 5곳을 남긴다.
  - 조건 판정은 카탈로그 근거로 만든다. 모두 pass이면 `recommend`, 아니면 `cannot_confirm`과 확인 주제를 붙인다.
  - 조건 실패를 빼고도 남는 후보가 하나도 없으면 모델 출력과 관계없이 `impossible`이다.
  - 후보는 있는데 고른 ID가 하나도 남지 않으면 지금처럼 차단한다. **서버가 장소를 대신 고르지 않는다.**
  - 조립한 응답은 기존 엄격 최종 검증을 다시 통과해야 한다.
  - 웹 요청에는 날짜 충돌 정보가 없으므로 `ask_clarification`은 만들지 않는다.
  - 기존 전체 형식 응답의 엄격 검증과 후보 복구는 그대로다. 그래서 되돌린 Vertex 흐름과도 호환된다.
- **Langflow Gateway 흐름**: 같은 흐름 ID·API 키를 유지하고 Agent 노드만 바꾼다.
  - 모델 클라이언트: 검토된 Vertex 생성 코드를 OpenAI 전용 코드로 교체한다. 주소 `api.openai.com`·모델 `gpt-5.4-mini`는 고정이며, 추론 강도 low, 재시도 1회, 60초 제한이다.
  - 자격증명: 새 Langflow Credential 변수 `JEJU_OPENAI_API_KEY`를 쓴다.
  - 시스템 지침: `placeIds`만 출력하도록 바꾼다.
  - 출력 한도: 4096토큰. 추론 토큰이 출력 한도에 함께 들어가기 때문이다.
  - 모델 8회(마지막은 도구 없는 답변 전용)·90초·도구 3종·최종 검증 노드는 그대로다.
  - Vertex 자격증명은 되돌리기용으로 남긴다.
- **바뀌지 않는 것**: Gateway·대기열·인증·Tunnel·Vercel 앱·웹 화면 계약.
- **데이터 흐름의 변화**: 설문 답변이 Google Vertex 대신 **OpenAI**로 전달된다. Vercel 앱의 여행 도우미·번역은 이미 OpenAI로 보내고 있다.
- **OpenAI 키**: Vercel과 같은 계정이어도 AWS용 **별도 프로젝트 키**와 사용 한도를 권장한다. 그래야 사용량을 구분하고 따로 폐기할 수 있다.

### 검증한 것과 하지 않은 것

- MCP 단위 테스트(`gateway-scopes`) 4개를 추가했다. 대상은 선택→조립, 5곳 상한·순서, 후보 없음→`impossible`, 남는 ID 없음→차단, 재검증 멱등성이다. jeju-mcp 245개, agent-gateway 77개가 통과했다.
- 흐름 변환·전환·되돌리기 테스트(`test_gateway_bootstrap`) 3개를 추가했다.
  - Agent의 모델 클라이언트·키 이름·모델 값·지침·출력 한도·설명만 바뀐다.
  - OpenAI 키가 없으면 PATCH 전에 거부한다. 되돌리기는 키 없이 가능하다.
  - 4회 흐름에서 바로 전환하지 않는다.
  - prepare-gateway(1개), 적용 helper(7개), 키 등록(4개) 테스트도 추가했다. 기존 테스트와 함께 deploy 145개 중 144개가 통과했다. 나머지 1개(`test_export_catalog`)는 main에서도 이 작업 환경의 git 제한으로 실패한다.
- 실제 981곳 카탈로그로 오프라인 조립 검사(`deploy/tests/check-rule-action.mjs`)를 통과했다. 모델과 네트워크는 쓰지 않았다.
- langchain-openai 1.8.5로 저장될 Agent 코드의 클라이언트를 만들어 요청 본문을 확인했다(네트워크 없음).
  - 본문은 `model:gpt-5.4-mini`, `stream:true`, `reasoning_effort:low`, `max_completion_tokens:4096`이다. `temperature`와 `max_tokens`는 없다.
  - `tool_choice="none"` 바인딩도 유지된다.
- CI(`agent-gateway-docker.yml`)는 공식 Langflow 1.13.3에서 다음을 확인하도록 했다.
  - 8회 흐름→규칙 흐름→8회 흐름 교체.
  - 키 없음 거부.
  - 흐름 고정 검사.
  - 가짜 모델 8회 예산 검사.
  - 저장된 코드로 OpenAI 클라이언트를 네트워크 없이 생성.

  PR #32 CI(`3d2e0d6`)에서 모두 통과했다.
  - 키 없음 거부: `GATEWAY_CREDENTIAL_UNAVAILABLE`, 표시 파일 없음.
  - 전환: `gateway-rule-action-applied`(`updated`, 재실행은 `already-current`).
  - 흐름 고정: `flowVerified:true`, `gateway-rule-action-checked`.
  - 가짜 모델 8회: 마지막 호출에서 JSON.
  - 클라이언트: `openaiClientInitialized:true`, `networkCalls:0`.
  - 되돌리기: `gateway-rule-action-rolled-back` 뒤 8회 흐름 고정 확인.

  첫 실행은 이 단계가 Langflow 로그인 제한(분당 5회)을 넘어 실패했다. 단계 안에 대기를 넣어 해결했다. 운영 helper는 실행당 로그인이 2회라 해당하지 않는다.
- **하지 않은 것**:
  - 실제 gpt-5.4-mini 호출.
  - Langflow 1.13.3 Agent가 OpenAI 모델 값으로 실제 실행되는지.
  - 장소 선택 품질, 차단 감소, 지연, 비용.
  - 사용자 AWS 적용.

### AWS에 적용할 명령

PR이 main에 병합된 뒤, 웹에서 새 추천을 제출하지 않는 때 AWS SSH에서 **한 줄씩** 실행한다.

```bash
cd ~/oreumit-agent && git diff --quiet && git diff --cached --quiet && git fetch origin refs/heads/main:refs/remotes/origin/main && git merge --ff-only origin/main
```

OpenAI 키를 Langflow Credential로 등록한다. 입력은 화면에 표시되지 않으며 키는 명령 인자·환경변수·파일·로그에 남지 않는다. OpenAI를 호출하지 않는다. 같은 이름 변수가 이미 있으면 `existing_preserved`로 그대로 둔다. 바꿔야 하면 Langflow 관리 화면에서 변수를 지운 뒤 다시 실행한다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/set-openai-key.sh
```

성공하면 `{"status":"configured_without_inference",…}`가 나온다. 이어서 적용한다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh
```

helper는 다음 순서로 진행한다.

1. Compose 설정을 확인하고, 이전 교체가 미완료인지 확인한다.
2. 새 MCP 이미지를 빌드한다. 기존 MCP는 계속 서비스한다.
3. 유휴 확인 뒤 `jeju-mcp`를 재생성한다.
4. 설치된 MCP로 오프라인 조립 검사를 한다.
5. 다시 유휴를 확인하고 `prepare-gateway.py --rule-action-flow`를 실행한다.
6. `agent-gateway`만 재생성한다.
7. `--check-rule-action-flow`로 흐름 고정을 확인한다.
8. 공개 인증 준비 검사와 큐 준비 검사를 한다.
9. 미완료 표시를 해제한다.

MCP를 먼저 바꾸는 이유는 새 MCP만 `placeIds` 응답을 조립하기 때문이다. 유휴·중단·재개의 의미는 [모델 호출 예산 조정](#중단됐을-때)과 같다. 종료 코드 75는 진행 중 요청이 있다는 뜻이며 모두 끝난 뒤 다시 실행한다. 저장 흐름이 검토된 8회 Vertex 빌드와 다르면 `GATEWAY_EXISTING_FLOW_DIFFERS`로 거부한다. OpenAI 키가 없으면 `GATEWAY_CREDENTIAL_UNAVAILABLE`로 PATCH 전에 멈춘다.

성공 출력:

```json
{"status":"passed","scope":"agent-rule-action-applied","model":"gpt-5.4-mini","modelCallLimit":8,"services":["jeju-mcp","agent-gateway"],"modelExecuted":false,"productionReady":false}
```

적용은 모델을 실행하지 않는다. 적용 뒤 로그인한 웹 사용자로 추천 1건을 확인하고, 실행이 쌓이면 위 [확인 SQL](#적용-전후-확인-sql)로 전환 전후를 비교한다. 이 전환도 흐름의 `updated_at`을 바꾸므로 기간 경계가 이 시각으로 옮겨진다. 수락 결과의 `action`은 서버가 조립한 값이다.

### 확인과 되돌리기

규칙 흐름 상태만 재시작 없이 확인한다. 적용 뒤에는 `apply-agent-call-budget.sh --verify-only`가 저장 흐름 불일치로 실패하므로 이 명령을 쓴다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh --verify-only
```

8회 Vertex 흐름으로 되돌린다. `agent-gateway`만 재생성하고 MCP는 새 이미지를 유지한다. 새 MCP는 전체 형식 응답을 이전과 같게 검증한다. 되돌린 뒤에는 `apply-agent-call-budget.sh --verify-only`가 다시 통과해야 한다.

```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh --rollback
```

되돌리기를 검토할 기준은 위 예산 조정과 같다. 적용 후 20건 이상이 쌓인 뒤 비율로 판단한다.
- `blocked_pct`가 줄지 않는다.
- `agent_error_pct`가 늘어난다. OpenAI 인증·한도 오류(401·429)도 Agent 오류로 나타난다.
- 지연이나 비용을 받아들일 수 없다.

`productionReady:false`를 유지한다.

## 공식 참고 링크

- [Named remotely managed Tunnel 생성·Published application route](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
- [공개 self-hosted Access 앱—route보다 먼저 보호 설정](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
- [서비스 토큰 생성·만료·요청 헤더](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)
- [Access 정책 관리](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/policy-management/)
- [Service Auth 정책과 Service Token 조건](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)
- [Access AUD·origin JWT 검증](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
- [cloudflared token-file 옵션](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/#token-file)
- [cloudflared 2026.11.1 릴리스](https://github.com/cloudflare/cloudflared/releases/tag/2026.11.1)
- [실제 ready 명령 구현](https://github.com/cloudflare/cloudflared/blob/2026.11.1/cmd/cloudflared/tunnel/subcommands.go#L467)

메뉴명은 2026-11-19 공식 `cloudflare/cloudflare-docs` production 소스 `71887c4dd651ecb8718833af10feb53ac7433cf6`를 확인한 것이며 사용자의 실제 계정 화면을 열어 본 결과는 아니다. 계정 UI에 따라 일부 명칭·배치가 다를 수 있다. 이전 AWS 성공 근거는 [31 문서](31-lightsail-langflow.md)를 따른다.
