# 자체 도메인 점검·서비스 선택·MCP 연계와 Ollama 연결 안내

기준일: 2026-11-18 한국 시간. 사용자는 `oreumit.com`에 Vercel **Valid Configuration**이 표시되고 Cloudflare 지정 네임서버 저장을 마쳤다고 전달했다. 기존 웹 소스의 기본 주소는 `jeju-tour-six.vercel.app`이며 Ollama 호스트는 Windows·macOS 두 장비를 대상으로 한다. 이 문서는 **사용자 보고·직접 확인한 로컬 구현·아직 검증하지 못한 운영 상태를 구분한 실행 안내**다. 에이전트가 도메인 구매·DNS 변경·OAuth 연결·공개 터널 실행·사용자 장비 설치를 수행한 것은 아니다.

최신 구현 범위와 바로 이어갈 작업은 [다음 단계 및 MCP 구현 현황](13-next-steps-and-mcp-implementation.md)을 기준으로 확인한다.

## 1. 현재 자체 도메인이 있는가

**이제 기존 `oreumit.com`을 재사용하는 방향으로 진행한다.** 사용자가 전달한 Vercel Valid Configuration과 네임서버 저장은 설정 진행의 근거다. 다만 에이전트가 Vercel 계정의 등록 목록, Cloudflare DNS의 Active 상태, 실제 위임·레코드·TLS를 직접 조회해 검증한 것은 아니다. Vercel 표시만으로 Cloudflare 활성화나 새 하위 도메인의 연결까지 완료됐다고 보지 않는다.

| 확인 대상 | 확인한 사실 |
|---|---|
| 사용자 보고: Vercel | `oreumit.com`에 Valid Configuration 표시. 에이전트의 계정 직접 조회 결과는 아님 |
| 사용자 보고: Cloudflare 네임서버 | `dilbert.ns.cloudflare.com`, `irma.ns.cloudflare.com` 저장 완료. 등록기관 저장과 실제 DNS 위임·Cloudflare Active는 구분 |
| 웹 원본 README | `jeju-tour-six.vercel.app`을 운영 주소로 안내 |
| Supabase 문서 | 같은 주소를 Site URL·로그인 Redirect URL에 사용 |
| `naver-auth.ts` | 같은 기본 주소로 로그인 복귀하도록 작성된 부분이 있음 |
| 웹 `client/lib/api.ts` | 동일 출처 `/api`를 사용 |
| Flutter `vercel.json`, `lib/core/api.dart` | 같은 Vercel 기본 주소로 웹 프록시/네이티브 API 연결 |
| 원본 서비스의 터널·MCP | 조사한 원본 설정에 공개 HTTP MCP 도메인·cloudflared 라우팅은 없고 기존 stdio MCP는 Vercel에서 비활성화됨. 이 사실은 이후 사용자가 설정한 계정 상태를 부정하지 않음 |
| 새 로컬 MCP 구현 | `services/jeju-mcp`에 읽기 전용 3개 도구 구현, 테스트 24개 통과, 참고 POI 7,561건을 로컬 카탈로그로 가져옴. 공개 배포·운영 준비 완료는 아님 |
| 실제 계정/DNS | 이 세션에는 Vercel·Cloudflare 계정 조회 도구가 없음. 공개 RDAP 요청은 환경 프록시 403, `oreumit.com`·`www.oreumit.com` DNS 조회는 일시적 이름 해석 실패로 등록·위임·TLS 상태를 확인하지 못함. 조회 실패는 도메인 부재의 증거가 아님 |

남은 확인은 **Vercel → 해당 프로젝트 → Settings → Domains**에서 `oreumit.com`·`www.oreumit.com`의 실제 등록·리다이렉트 목록을 보고, Cloudflare에서 `oreumit.com` zone의 Active 상태와 DNS 레코드를 확인하는 것이다. 두 웹 주소가 모두 연결됐다고 가정하지 않는다. 기존 소스가 기본 Vercel 주소를 사용한다는 이유로 이미 확보한 도메인을 다시 구매할 필요는 없다.

`vercel.app`은 Vercel이 관리하는 도메인이다. 이 기본 주소를 사용자의 Cloudflare DNS zone으로 등록하거나 `ollama.jeju-tour-six.vercel.app`을 마음대로 만드는 방식은 사용할 수 없다.

## 2. 기존 도메인을 재사용하는 서비스 구성

**추천: `oreumit.com`의 기존 등록기관을 유지하고, Cloudflare DNS·Tunnel과 기존 Vercel 웹 프로젝트를 연결한다.** 새 도메인 구매나 Cloudflare Registrar로의 이전은 현재 작업의 선행 조건이 아니다.

| 구성 요소 | 이번 작업의 선택 | 확인할 점 |
|---|---|---|
| 도메인 등록 | 기존 `oreumit.com` 재사용 | 실제 등록기관·갱신 설정·관리 권한 확인. 이미 확보한 도메인을 중복 구매하지 않음 |
| 권한 DNS | 사용자 보고의 Cloudflare 지정 네임서버 사용 | `dilbert.ns.cloudflare.com`, `irma.ns.cloudflare.com` 저장 후 실제 위임과 zone Active 확인 |
| 웹 호스팅 | 기존 Vercel 프로젝트 유지 | apex·www의 등록 목록과 Vercel이 제시하는 정확한 DNS 값 확인 |
| 서버 연결 | 역할별 Cloudflare Named Tunnel | 먼저 인증 정책과 상시 실행 호스트를 정하고 MCP·선택적 Ollama Gateway만 연결 |

Cloudflare의 일반 무료/Pro DNS full setup은 소유한 apex 도메인의 nameserver를 지정값으로 바꾸는 구성이다. **기존 웹 A/CNAME, 메일 MX, SPF/DKIM/DMARC와 서비스 검증용 TXT, 필요한 CAA 등 중요 레코드를 확인해 보존한다.** Cloudflare가 자동으로 가져온 목록도 원본과 대조한다. DNS를 Cloudflare로 관리한다고 앱 호스팅까지 Vercel에서 옮기는 것은 아니다.

기존 도메인의 갱신비와 서버/GPU 비용은 기본 Tunnel 무료 제공과 별개다. 여기서는 구매·등록기관 이전·자동 갱신 결제 변경을 진행하지 않았다.

## 3. 권장 주소 구조

아래는 `oreumit.com`을 기준으로 한 **제안 라우팅**이다. 하위 도메인의 DNS·Tunnel·TLS가 이미 배포됐다는 뜻이 아니다.

| 주소 | 연결 대상 | 현재 상태·초기 적용 |
|---|---|---|
| `jeju-tour-six.vercel.app` | 현재 Vercel 앱 | 그대로 사용 가능. 백엔드 도메인부터 추가하면 로그인 도메인을 즉시 바꿀 필요가 없음 |
| `oreumit.com` / `www.oreumit.com` | 같은 Vercel 웹 프로젝트 | apex Valid Configuration은 사용자 보고. 두 주소의 등록 목록·리다이렉트·로그인 호환은 확인 필요 |
| `mcp.oreumit.com/mcp` | 제주 관광 읽기 전용 MCP 서버 | 로컬 구현 기본 주소 `127.0.0.1:8788`. 상시 실행 호스트에 옮겨 인증된 Named Tunnel로 연결할 예정; 공개 배포 전 |
| `agent.oreumit.com` | 향후 Agent Gateway/작업 API | 장시간 작업 접수·조회·취소는 아직 미구현. 모델 공급자 선택과 비용 제한을 Worker에 구현할 예정 |
| `ollama-win.oreumit.com` | Windows의 보호된 추론 Gateway | 선택 사항. 같은 장비의 `127.0.0.1:8787` Gateway로 연결하며 원시 Ollama 포트를 공개하지 않음 |
| `ollama-mac.oreumit.com` | macOS의 보호된 추론 Gateway | 선택 사항. 같은 장비의 `127.0.0.1:8787` Gateway로 연결; 사용자 장비에서 실제 추론 확인 필요 |

웹 도메인을 추가할 때 Vercel이 해당 프로젝트에 표시하는 최신 A/CNAME 값을 사용한다. 과거 예제의 고정 IP를 복사하지 않는다. Tunnel 호스트명은 cloudflared 설정이 관리하는 목적지로 연결하고 같은 이름에 충돌하는 Vercel 레코드를 만들지 않는다.

OpenAI·Claude 호출은 Worker의 outbound HTTPS다. 이 두 공급자를 위해 별도 터널·DB를 만들지 않는다. Windows·Mac의 Ollama는 자원·모델·가용성이 다를 수 있으므로 라우터/작업 스케줄러가 호스트를 선택한다. 서로 다른 모델 호스트를 같은 터널 replica로 등록하고 자동으로 올바른 모델에 전달될 것이라고 가정하지 않는다.

더 단순하게 각 GPU 호스트의 Worker가 DB에서 작업을 가져와 로컬 Ollama를 호출하면 두 Ollama 공개 호스트명도 필요 없다. 원격 HTTP 호출을 선택할 때만 위 보호된 주소를 사용한다.

## 4. MCP는 두 종류를 구분한다

### 계정·인프라 관리용 공식 MCP

공식 소스에서 아래 원격 서버 주소를 확인했다. 이 세션에 연결되어 있거나 OAuth 승인이 완료된 상태는 아니다.

| 서버 | 공식 MCP 주소 | 확인 범위 |
|---|---|---|
| Vercel 관리 MCP | `https://mcp.vercel.com` | 공식 CLI에서 endpoint·인증 안내 확인. 실제 계정에서 제공되는 도메인 조회 도구·권한은 연결 후 확인 필요 |
| Cloudflare API MCP | `https://mcp.cloudflare.com/mcp` | 공식 README에서 OAuth와 `docs`, `search`, `execute`, `whoami` 도구 확인. DNS API 지원을 확인했으며 Tunnel 작업은 실제 제공 operation을 검색해 확인 |

원격 MCP를 지원하는 클라이언트의 설정에서 공식 URL을 등록하고 본인 계정으로 OAuth를 승인한다. 이후 실제 노출된 도구로 계정·프로젝트·DNS를 **조회**해 현재 상태를 확정한다. API 토큰 대안을 쓰는 경우에도 필요한 계정/zone·최소 권한으로 제한하고 채팅에 값을 붙이지 않는다. 연결한 사실이 구매·DNS 변경·삭제를 자동 승인하는 것은 아니다.

현재 이 대화에는 두 MCP의 조회·변경 도구가 제공되지 않아 직접 계정 확인이나 변경을 할 수 없다. URL을 문서에 적는 것만으로 MCP가 연결되지는 않는다.

### 관광 서비스용 자체 MCP

`mcp.oreumit.com/mcp`는 우리가 구현한 첫 제주 관광 MCP 서버를 공개할 **예정 주소**다. Cloudflare/Vercel의 계정 관리 MCP와 역할이 다르다. [로컬 서비스](../services/jeju-mcp/README.md)에 `places.search`, `places.get`, `constraints.check` 3개 읽기 전용 도구를 구현했고 테스트 24개가 통과했다. 기본 수신 주소는 `127.0.0.1:8788`이며 공개 배포는 수행하지 않았다.

참고 저장소의 POI 7,561건을 출처 커밋과 함께 로컬 카탈로그로 가져왔다. 원본 ID·분류·좌표를 보존하지만 필드별 근거·제약은 새로 추정하지 않아 `evidence: []`, `constraints: {}`이며, 근거 없는 접근성·실내·비용 조건은 `unknown`이다. 이 수치를 운영 검증된 관광지 수나 데이터 이용권 확인 수로 해석하지 않는다.

초기 서비스에는 Bearer 인증과 읽기 도구 계약이 있다. [전체 MCP 구축 계획](10-mcp-server-and-vercel-integration.md)의 사용자 위임·개인별 소유권·쓰기 도구·작업 큐·모델 어댑터·기존 Vercel UI 통합은 아직 완료하지 않았다. 향후 OpenAI·Claude·Ollama를 호출하는 공통 Worker가 이 MCP를 사용한다. 일반 사용자에게 DNS·배포·계정 관리 도구를 제공하지 않는다. 상세 완료·미완료 구분은 [최신 구현 현황](13-next-steps-and-mcp-implementation.md)에 따른다.

## 5. Windows·macOS에서 지금 해볼 연결 시험

`oreumit.com`의 DNS 전환을 기다리는 동안 **인증 Gateway + Quick Tunnel**로 짧은 연결 시험을 할 수 있다. 이는 선택 사항이며 고정 Named Tunnel 구성에 앞서 반드시 거쳐야 하는 단계가 아니다. 임시 `trycloudflare.com` 주소는 운영 고정 주소를 대체하지 않으며, SSE 미지원이므로 non-streaming 요청으로 시험한다.

[Windows·macOS 단계별 안내](../examples/ollama-tunnel/README.md)와 [다운로드 ZIP](../reports/ollama-tunnel-starter.zip)에 필요한 예제를 제공한다. 계정·도메인 구매 없이 사용자 PC에서 직접 실행하는 순서다.

1. 각 PC에 네이티브 Ollama, 현재 지원되는 Node.js LTS, cloudflared를 준비한다.
2. 로컬 모델을 설치하고 짧은 응답을 확인한다. `llama3.2:3b`는 연결 시험 예시이며 제주 추천의 최적 모델이라는 주장이 아니다.
3. `node configure.mjs <정확한 설치 모델 태그>`로 장비별 `.env.local` 토큰을 만든다. 키는 출력·공유하지 않는다.
4. `node --env-file=.env.local gateway.mjs`로 `127.0.0.1:8787` Gateway를 실행한다.
5. `node --env-file=.env.local check.mjs`로 로컬 인증·차단 경로·짧은 추론을 확인한다.
6. 별도 터미널에서 `cloudflared tunnel --url http://127.0.0.1:8787`을 실행한다.
7. 출력된 본인의 임시 URL을 `check.mjs`에 전달해 원격 왕복을 확인한다. 두 장비에서 각각 실행한다.

이 Gateway는 정확한 `/api/chat` 요청, 고정 모델, 제한된 입력·출력·시간, 동시 요청 1개만 허용한다. 공개 무인증 Ollama API를 직접 전달하지 않는다. 사용자 PC·터널·모델이 실제로 연결됐는지는 위 명령 실행 결과로 확인해야 한다. 저장소 테스트는 모의 localhost 업스트림만 사용한다.

Vercel 환경변수 등록만으로 이 연결이 기존 추천 UI에 붙지는 않는다. 후속 Ollama 어댑터·호스트 라우팅·사용자 인증·작업 큐 구현이 필요하다. 긴 추천 작업은 기존 [비동기 작업 설계](10-mcp-server-and-vercel-integration.md)를 따른다.

## 6. DNS 위임 확인 후 고정 Named Tunnel과 MCP를 연결하는 순서

1. 사용자 보고의 `oreumit.com` 네임서버 저장 이후 실제 위임과 Cloudflare Active를 확인하고 기존 DNS 중요 레코드를 대조한다. 각 호스트 역할과 상시 실행 장비를 정한다. 기존 웹의 기본 주소는 병행 유지할 수 있다.
2. 이번에 Tunnel로 공개할 MCP·추론 Gateway의 정확한 hostname 전체에 Cloudflare Access 애플리케이션을 먼저 설정한다. 기존 공개 웹앱에 이 서버 간 인증 정책을 일괄 적용하지 않는다. 공식 문서의 UI는 `Access controls → Applications → Create new application → Self-hosted and private`다.
3. 서버 간 호출용 Service Token을 만들고 정책을 `Service Auth`, 대상은 지정한 토큰으로 설정한다. 일반 `Allow` 정책의 대화형 로그인 흐름과 혼동하지 않는다.
4. `Networking → Tunnels → Create Tunnel`에서 host별 이름을 정하고 OS·아키텍처를 선택한다. 화면의 설치·실행 명령을 **대상 사용자 PC/서버**에서 실행한다. Connector 토큰은 비밀값이다.
5. 연결 상태 확인 후 `Routes → Add route → Published application`에서 hostname과 같은 호스트의 로컬 서비스 주소를 등록한다. `mcp.oreumit.com`은 `http://127.0.0.1:8788`, 선택적 `ollama-win.oreumit.com`·`ollama-mac.oreumit.com`은 해당 장비의 `http://127.0.0.1:8787`로 연결한다. 호출 경로는 MCP의 `/mcp`와 Ollama Gateway의 `/api/chat`을 구분한다. 이 작업공간의 localhost를 사용자 PC에서 사용할 수 있는 주소로 오인하지 않는다.
6. `Protect with Access` 등 지원 설정으로 cloudflared의 Access JWT 검증을 적용하고 issuer/audience 등 해당 앱의 값을 확인한다. Windows/WSL·컨테이너 사이에서 서로 다른 localhost를 혼동하지 않는다.
7. 서버 호출자는 `CF-Access-Client-Id`, `CF-Access-Client-Secret`을 전송한다. 현재 MCP 서버와 Ollama Gateway의 자체 Bearer 토큰도 각각 필요하다. 향후 사용자 위임 토큰·scope·소유권 검사를 추가할 때는 네트워크 진입 인증과 별도로 적용한다. 초기 읽기 전용 서비스가 해당 전체 인증 체계를 이미 구현한 것은 아니다.
8. 무인증 차단, 정상 서비스 인증, 금지 경로 차단, 짧은 추론 또는 실제 MCP 초기화·도구 호출을 검증한다. 열린 URL이나 HTTP 200만으로 MCP가 구현됐다고 판단하지 않는다.
9. 첫 공개 단계에서는 읽기 도구의 동작과 재시작·네트워크 단절을 검증한다. 이후 작업 큐·모델 어댑터·절전 대응·호스트별 동시성·취소를 구현하고 검증한 뒤 기존 Vercel UI의 제한된 사용자에게 연결한다. DNS·Tunnel 준비만으로 전체 추천 기능이 연결됐다고 표시하지 않는다.

Tunnel 실행 토큰, Access 서비스 토큰, Cloudflare 관리 API 토큰은 서로 다르다. 서버 전용 비밀값은 브라우저 번들에 넣지 않는다. 이 순서는 현재 계정 화면을 직접 조작한 결과가 아니라 고정 공식 소스 기준 안내이며 UI 이름은 계정/제품 업데이트에 따라 달라질 수 있다.

웹 자체를 새 도메인으로 전환하는 단계에서는 Supabase Site/Redirect allowlist, Naver 로그인 복귀 주소, Flutter 프록시·API URL, 허용 Origin과 필요한 공급자 제한을 함께 확인한다. Supabase 공급자 callback 주소까지 무조건 새 웹 도메인으로 바꾸지 않고 실제 로그인 흐름을 기준으로 검증한다.

## 7. 근거

- 원본 웹 소스 커밋 `b0602a175c1d08959b7187da2b29ac450b52e0ba`, Flutter `81e5f2acc1b5fd947b183fe1d74b6707717518e7`. [웹 README](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/README.md), [Supabase URL 설정](https://github.com/OREUM-IT/jeju-tour/blob/b0602a175c1d08959b7187da2b29ac450b52e0ba/prototype/supabase/README.md).
- [Cloudflare 도메인 등록 정책](https://github.com/cloudflare/cloudflare-docs/blob/70104effd4fc45829b2dbabf929eae4f9d44ea9b/src/content/docs/registrar/get-started/register-domain.mdx), [DNS full setup](https://github.com/cloudflare/cloudflare-docs/blob/70104effd4fc45829b2dbabf929eae4f9d44ea9b/src/content/docs/dns/zone-setups/full-setup/setup.mdx).
- [Vercel nameserver 변경 SDK](https://github.com/vercel/sdk/blob/c6ec66707ea3b2da9001609c7186d0c18b763ac4/docs/sdks/domainsregistrar/README.md#updatedomainnameservers), [도메인 권고 DNS 값](https://github.com/vercel/sdk/blob/c6ec66707ea3b2da9001609c7186d0c18b763ac4/docs/models/getdomainconfigresponsebody.md).
- [Cloudflare API MCP 공식 README](https://github.com/cloudflare/mcp/blob/b99e945ca62a518a794d4a0b4e6c453b30446fb1/README.md), [Vercel MCP 공식 CLI](https://github.com/vercel/vercel/blob/155eacfa16840e68ae54bc50ee72fc8e16733857/packages/cli/src/commands/mcp/mcp.ts).
- [Tunnel 생성·연결](https://github.com/cloudflare/cloudflare-docs/blob/70104effd4fc45829b2dbabf929eae4f9d44ea9b/src/content/docs/tunnel/get-started/index.mdx), [Quick Tunnel 제한](https://github.com/cloudflare/cloudflare-docs/blob/70104effd4fc45829b2dbabf929eae4f9d44ea9b/src/content/docs/tunnel/get-started/quick-tunnels/index.mdx).
- [Access 앱 설정](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/), [Service Token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/), [Access origin 검증 고정 소스](https://github.com/cloudflare/cloudflare-docs/blob/70104effd4fc45829b2dbabf929eae4f9d44ea9b/src/content/partials/cloudflare-one/tunnel/origin-parameters.mdx).
- [Ollama 기본 바인딩·운영 FAQ](https://github.com/ollama/ollama/blob/b8db8d5b5c8daedbbef7bb9ace2221a38bc384c2/docs/faq.mdx), [로컬 API 인증 조건](https://github.com/ollama/ollama/blob/b8db8d5b5c8daedbbef7bb9ace2221a38bc384c2/docs/api/authentication.mdx).
