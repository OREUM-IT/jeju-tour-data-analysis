# Hugging Face 조직·MCP·CI 운영 설정

기준일: 2026-11-18 한국 시간. GitHub 대상은 `OREUM-IT/jeju-tour-data-analysis`이며 Hugging Face 조직 ID는 기존 `oreumit.com` 도메인에 맞춘 **`oreumit` 제안값**이다. 해당 ID의 사용 가능 여부와 새 로그인 계정의 조직 관리자 권한은 아직 확인하지 못했다.

## 1. 현재 구현·검증 범위

| 항목 | 상태 |
|---|---|
| Codex MCP 설정 | 프로젝트 `.codex/config.toml`에 공식 HTTPS 서버·읽기 도구 3개·시간 제한 설정 |
| Claude Code MCP 설정 | 프로젝트 `.mcp.json`에 공식 files preset 설정 |
| 독립 MCP 클라이언트 | 모델·데이터셋·논문 검색/읽기 명령과 로컬 허용 목록 구현 |
| 자동 CI | `.github/workflows/ci.yml` 구현. 기반 커밋 `ad3c363`의 main·전용 브랜치 GitHub Actions 성공 확인. 이번 PR에서 문서·메타데이터 검사도 추가 |
| 실제 HF 연결 CI | `.github/workflows/huggingface-mcp-smoke.yml` 구현. main에서 수동 익명 조회 |
| 로컬 확인 | 잠금 의존성 재설치 후 MCP·HF 클라이언트 32개 + Gateway 10개 = **42개 통과**. actionlint 1.7.7 통과 |
| HF 조직 생성·계정 연결 | 로그인한 브라우저의 조작 도구와 인증된 HF 접근이 없어 미실행 |
| 실제 HF 응답 | 클라우드의 해당 도메인 접속은 프록시 403. 실제 연결 성공 미확인 |

기반 커밋의 [main 실행](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44226883163)과 [전용 브랜치 실행](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44226628447)은 GitHub 공식 실행 페이지에서 성공을 확인했다. 이 CI 성공은 로컬 fixture 검사 결과이며 HF 조직 생성·공개 HF 실연결 성공을 뜻하지 않는다. 공개 MCP 조회는 조직 생성 전에도 가능한 별도 기능이다.

## 2. 조직 생성과 계정 확인

1. 사용자 요청대로 로그인한 새 HF 계정의 프로필을 확인한다. 이전 계정과 혼동하지 않고 생성 주체를 기록한다.
2. [조직 생성 화면](https://huggingface.co/organizations/new)에서 조직 ID `oreumit`, 표시 이름 `OREUM IT`, 웹사이트 `https://oreumit.com`을 기준으로 입력한다. ID가 이미 사용 중이면 그 조직의 소유권을 먼저 확인한다.
3. 무료 조직 범위로 생성한다. Team/Enterprise·결제·멤버 초대는 이 기본 구성에 필요하지 않다.
4. Members에서 생성 계정이 `admin`인지 확인한다. 실제로 생성된 조직 URL과 관리자 계정 확인 후 다음 단계로 진행한다.

위 계정 생성·설정 조작은 사용자가 승인했지만, 현재 세션에는 해당 브라우저 탭을 읽거나 클릭할 도구가 제공되지 않는다. 사용자 브라우저의 로그인은 클라우드의 HTTP 클라이언트에 인증으로 전달되지 않는다. 쿠키·토큰을 추출하거나 채팅에 붙이는 방식으로 옮기지 않는다.

## 3. MCP 계정 연결

공식 서버는 `https://huggingface.co/mcp`다. 조직마다 MCP 서버를 새로 설치하지 않는다. [개인 계정 MCP 설정](https://huggingface.co/settings/mcp)에서 사용할 도구를 정하고 실제 클라이언트에서 새 계정으로 OAuth를 승인한다. OAuth 진입 주소는 `https://huggingface.co/mcp?login`이다.

현재 공개 검색용 프로젝트 설정은 토큰이 없는 상태로 사용할 수 있도록 구성했다. 공식 소스의 익명 허용 도구는 `hub_repo_search`, `hub_repo_details`, `hf_fs`다. 실제 운영 서버의 제공 목록·인증 요구는 연결 후 확인한다. 사용자 설정의 도구 노출 목록만을 서버 권한 통제로 간주하지 않고 Codex와 독립 클라이언트의 허용 목록을 유지한다.

조직의 비공개 저장소를 읽을 때만 해당 조직·필요 저장소에 대한 읽기 권한을 추가한다. 공식 OAuth 진단의 최소 범위는 `read-mcp`이며, 실제 클라이언트 승인 화면에서도 요청 범위를 확인한다. 저장소 쓰기·Jobs·Inference 권한은 이번 공개 조회에 필요 없다. OAuth 대신 토큰을 선택하면 fine-grained 사용자 토큰을 보안 설정으로 전달하고 사용자 역할과 토큰 범위 모두에서 접근이 허용되는지 확인한다.

아래 명령은 저장소 루트에서 실행한다. Node 24와 잠금 의존성이 필요하다.

```sh
npm --prefix services/jeju-mcp ci --ignore-scripts --no-audit --no-fund
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs list
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs datasets "Jeju tourism"
```

`list`가 허용 도구 3개를 반환하고 실제 검색까지 성공해야 공개 MCP 연결이 확인된다. 조직 비공개 자료에 대한 권한 검증은 별도의 의도한 저장소를 읽어 확인해야 한다. 익명 검색 성공으로 관리자 계정이나 조직 가입 상태를 추정하지 않는다.

## 4. GitHub Actions CI

### 자동 로컬 검사

[CI 워크플로](../.github/workflows/ci.yml)는 서비스·Gateway·MCP 설정·워크플로·문서·데이터·설계·보고서·스크립트 변경 시 실행한다. main 또는 `codex/**` 브랜치 push, PR, 수동 실행을 지원한다. 문서와 메타데이터 검사는 원본 CSV 체크아웃 없이 `python3 scripts/check_repository.py`로 재현한다.

- Ubuntu 24.04, Node.js **24.22.1**.
- 공식 `actions/checkout` v6.1.0, `actions/setup-node` v6.5.0의 실제 release commit SHA 고정.
- 권한 `contents: read`; checkout 자격증명 보존 해제; PR에 비밀값 전달 없음.
- `npm ci --ignore-scripts --no-audit --no-fund`로 잠금 의존성 설치.
- MCP JSON/TOML 구문, 서비스·HF 클라이언트 32개 테스트, Gateway 10개 테스트.
- 테스트는 localhost fixture와 임시 Git 저장소를 사용한다. GPU·외부 원본 CSV 체크아웃·실제 모델 API가 필요하지 않다.

### 수동 실제 연결 검사

[HF 연결 워크플로](../.github/workflows/huggingface-mcp-smoke.yml)는 기본 브랜치에 반영된 뒤 GitHub Actions에서 **Hugging Face MCP anonymous smoke → Run workflow → main**으로 실행한다. main 이외의 ref는 실행 대상에서 제외한다.

이 검사는 익명으로 고정 HF endpoint의 초기화·도구 목록·모델/데이터셋 검색을 수행한다. 각 검색은 `Jeju tourism`, 최대 5개로 제한한다. 정상 응답에서 검색 결과가 없을 수도 있으므로 연결 성공과 관련 자료 발견은 구분한다. 도구 누락·부분 응답·잘림은 통과시키지 않는다.

CI에는 HF_TOKEN·조직 쓰기 권한·모델 실행·데이터 업로드가 필요 없다. 원격 검색 결과 본문과 오류 원문은 로그에 출력하지 않고, 명령·도구·상태만 기록한다. 조직 접근 검증이나 추론을 실행한 것으로 표시하지 않는다.

## 5. 상태 확인과 이후 확장

- [GitHub Actions 실행 목록](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions)
- [HF 조직 생성](https://huggingface.co/organizations/new)
- [HF MCP 계정 설정](https://huggingface.co/settings/mcp)

기반 MCP·CI 커밋 `ad3c36384117696457d4c6ee547d9659629116fa`는 이미 main에 반영되어 자동 CI에 성공했다. 이번 전체 설계·모델 인수인계 변경은 별도 PR 대상으로 검토하며 자동으로 main에 병합하지 않는다. 수동 HF 연결 워크플로의 실제 실행은 아직 확인하지 않았다. GitHub API가 필요한 실행 조회·PR 생성·수동 dispatch에는 `api.github.com` 네트워크 접근과 해당 GitHub 계정 권한이 필요하다. Git 읽기·push 권한이 GitHub API 접근을 자동으로 보장하지 않는다.

HF Space 자동 배포·모델 업로드·사설 데이터 동기화는 CI와 별도 배포 작업이다. 실제 조직·대상 저장소·역할·비용을 확인한 다음 추가한다. 비공개 자료를 CI에서 읽게 될 때는 보호된 GitHub Environment와 읽기 전용 HF Trusted Publisher(OIDC) 또는 최소 범위 Secret을 검토한다. 현재 워크플로는 이 권한을 요구하지 않는다.

## 6. 연결 장애 기록

이번 작업 환경에는 브라우저 제어·런타임 네트워크 설정 적용·환경 게시 도구가 제공되지 않는다. 상태 조회와 설정 초안 저장은 가능하다. 공개 HF MCP 접속과 GitHub REST API 접속은 현재 프록시에서 거부되며, 허용 설정 초안의 저장과 실제 런타임 반영을 구분한다. 다음 세션에서는 도구와 접근 상태를 다시 조회한다.

## 7. 공식 근거

- [HF 조직 관리](https://github.com/huggingface/hub-docs/blob/main/docs/hub/organizations-managing.md)
- [HF 조직 권한](https://github.com/huggingface/hub-docs/blob/main/docs/hub/organizations-security.md)
- [HF 사용자 토큰](https://github.com/huggingface/hub-docs/blob/main/docs/hub/security-tokens.md)
- [공식 MCP OAuth 진단 범위](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/docs/oauth-diagnostics.md)
- [HF Trusted Publishers](https://github.com/huggingface/hub-docs/blob/main/docs/hub/trusted-publishers.md)
