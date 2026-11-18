# Claude Code 인수인계와 작업 재개 절차

기준일: 2026-11-18. 목표는 기존 Vercel 제주 관광 앱을 활용하는 개인화 Agent를 구현하고, 현재 분석·설계·데이터·MCP 작업을 하나의 검토 가능한 PR로 이어가는 것이다. 이 문서는 실행 가능한 초기 구현과 아직 필요한 외부 접근을 분리한다. 이전 문서에 남아 있는 과거 상태보다 아래의 확인 시점과 근거를 우선하고, 새 결과가 생기면 해당 근거와 함께 갱신한다.

## 1. 바로 이어받을 상태

| 항목 | 확인된 결과 | 아직 확인하지 않은 것 |
|---|---|---|
| 분석 저장소 | `OREUM-IT/jeju-tour-data-analysis`; 인수인계 작성 시 main과 `codex/huggingface-mcp-ci`의 기준 커밋은 `ad3c36384117696457d4c6ee547d9659629116fa` | 전체 자료를 포함하는 이번 PR의 최종 head는 이후 커밋이다. PR에서 확인 |
| CI | [실제 GitHub Actions 실행 성공](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44226883163); MCP·HF 모의 검사 32개와 Gateway 10개, 총 42개 | 실제 HF 운영 서버 연결이나 모델 추론 성공은 이 결과에 포함되지 않음 |
| 전체 작업 | `codex/ai-agent-model-handoff` 브랜치로 기획 문서, 데이터 카탈로그, 보고서, 시각화, 재생성 스크립트를 검토 | 인수인계 작성 환경의 GitHub API는 403. PR 생성 여부는 실제 GitHub 조회로 확인하며 Git 추적 파일을 인수인계 기준으로 사용 |
| 페르소나 | 9개 테마 + 4개 연속 선호 축, 총 13축의 수작업 프로토타입 8개; 독립 HTML 탐색기 | 실제 관광객 표본·학습·경험적 군집·추천 효과 검증 없음 |
| CSV | 55파일, 데이터 논리행 합계 210,532, 컬럼 기록 675; 원본 스냅샷 37·제주 추출본 17·파생 집계 1 | 라이선스 공란 8개·개별 조건 미확인 2개가 있어 원본 CSV ZIP은 공개 PR에서 제외. 최신성·중복·전체 Excel 대조도 미완료 |
| 제주 자체 MCP | `places.search`, `places.get`, `constraints.check`; 기존 JSON의 POI 7,561건 로컬 가져오기와 실제 SDK 통신 검사 | 공개 서버 배포·Vercel 연결·개인별 OAuth·추천 Agent 미완료 |
| HF MCP | 프로젝트 설정과 독립 클라이언트, 신규 HF 모의 검사 8개가 위 32개에 포함 | 실제 공개 접속은 현재 클라우드 프록시 403; 연결 성공 미확인 |
| HF 계정·조직 | 사용자가 다른 계정으로 로그인했다고 보고. 기본 조직 ID 제안은 `oreumit` | 실제 사용자명·조직 ID 가용성·관리자 권한 미확인, 조직 생성 미실행 |
| 도메인 | 사용자 보고: `oreumit.com`의 Vercel Valid Configuration, Cloudflare NS `dilbert.ns.cloudflare.com`·`irma.ns.cloudflare.com` 저장 | 계정 화면 직접 조회, 실제 DNS Active·전파·TLS·웹 alias 목록 미확인 |
| 모델 | 기존 앱 코드의 공급자 경로와 후보 모델을 문서화 | 이 작업에서 Ollama 적재·LLM 추론·성능·비용 실측 없음 |
| 연구 원자료 | `2504.09277` = SynthTRIPs(평가셋 생성 틀), LAG = `2508.05509`(2026-11-18 사용자 결정, [19 문서](19-agent-step2-data-foundation.md)) | 두 논문 본문 세부·별도 PDF·목록 Excel 1,259항목의 원본 미확보 |

55개 CSV는 7,561건 MCP 카탈로그와 다른 산출물이다. CSV 전체를 MCP에 적재한 상태가 아니다. 210,532는 고유 장소 수가 아니며 파생 수요 통계도 관광객 선호 정답이 아니다. `/readyz`의 `productionReady:false`는 실제 자료 모드에서도 유지한다.

## 2. 먼저 읽을 파일

1. [README](../README.md)와 이 문서: 전체 산출물·현재 상태.
2. [모델 레지스트리](17-ai-agent-model-registry.md), [기계 판독 모델 목록](../data/ai-agent-models.json): 기존 코드 기본값, 새 후보, 실행 미검증 상태. 이 목록은 자동 라우터가 아니다.
3. [HF MCP 설정](15-huggingface-mcp-setup.md), [HF 조직·CI](16-huggingface-organization-and-ci.md): 현재 가장 먼저 이어갈 연결 작업.
4. [기존 서비스 검토](05-existing-service-review.md), [MCP·Vercel 통합](10-mcp-server-and-vercel-integration.md): 실제 원본 계약과 제안의 경계.
5. [확보 CSV](14-confirmed-data-catalog.md), [데이터 계획](02-data-plan.md), [미확인 근거](06-evidence-and-open-items.md): 원본·권리·시점·조인 문제.
6. [페르소나 설계](08-persona-clustering.md), [Ollama·LAG 계획](09-ollama-lag-feasibility.md), [다중 공급자·DB](11-multi-provider-tunnels-database-and-cost.md).
7. [도메인 절차](12-domain-tunnel-and-ollama-runbook.md), [후속 배치와 MCP 상태](13-next-steps-and-mcp-implementation.md), [서버 실행 안내](../services/jeju-mcp/README.md).

Word/PDF에는 현재 문서 01~18을 편집 순서에 맞춰 포함한다. 문서 수정 후 자동 갱신되지는 않으므로 `scripts/build_report.py --pdf`로 다시 생성한다. 최신 운영 상태는 Markdown·실제 CI·코드에서 읽는다.

## 3. Git와 PR부터 보존

현재 환경의 분석 체크아웃은 `/workspace/jeju-tour-data-analysis`다. 이미 이 경로를 사용 중이면 새 clone이나 worktree를 만들기 전에 상태를 확인한다.

```sh
cd /workspace/jeju-tour-data-analysis
git status --short
git branch --show-current
git rev-parse HEAD
git log -5 --oneline
git diff --stat
git ls-files CLAUDE.md docs/18-claude-code-handoff.md
```

이전 기준 SHA로 강제 reset하지 않는다. 인수인계 이후의 변경과 사용자의 미커밋 작업을 보존한다. PR이 열려 있다면 실제 head ref·SHA·base·본문·검사 결과를 먼저 읽고 그 PR을 이어간다. 제목과 본문에는 전체 작업 범위, 42개 검사의 근거, HF 연결 차단, 미배포·미추론·미확보 원자료를 남긴다. 이미 있는 설명을 짧은 새 요약으로 덮어쓰지 않는다.

GitHub CLI와 정상 API 접근이 제공되는 환경에서는 다음 읽기 명령으로 확인할 수 있다. 도구가 없거나 프록시가 거부하면 이 명령을 반복하지 않고 사용 가능한 GitHub 연결/브라우저로 확인한다.

```sh
gh pr status --repo OREUM-IT/jeju-tour-data-analysis
gh pr view <실제-PR-번호> --repo OREUM-IT/jeju-tour-data-analysis \
  --json number,url,title,body,baseRefName,headRefName,headRefOid,state
```

`<실제-PR-번호>`는 실제 PR 조회 결과로 바꾼다. 본문 편집 전 현재 내용을 작업용 파일에 그대로 저장하고, 수정 후 대조한다. `gh pr edit`를 사용하면 실제 줄바꿈을 보존한 `--body-file`을 사용한다. 임시 본문 파일은 재개에 필요한 유일한 기록으로 삼지 않고 최종 내용을 PR 자체에 남긴다. PR 생성·업데이트가 이미 승인된 범위지만 자동 merge, 강제 push, 원본 앱 변경까지 승인된 것으로 확대하지 않는다.

PR이 아직 없고 API 접근이 복구되었다면 [준비된 PR 본문](PR_BODY.md)을 검토하고 아래 명령으로 생성한다. 먼저 `gh pr list --head codex/ai-agent-model-handoff`로 중복 여부를 확인한다.

```sh
gh pr create --repo OREUM-IT/jeju-tour-data-analysis \
  --base main --head codex/ai-agent-model-handoff \
  --title 'Update all AI AGENT MODEL: 제주 관광 Agent 전체 설계와 Claude Code 인수인계' \
  --body-file docs/PR_BODY.md
```

새 환경에는 분석 저장소를 새 경로로 clone한 뒤 **실제 전체 작업 PR의 head**를 checkout한다. 기준 main만 checkout하면 아직 merge되지 않은 설계·데이터·보고서가 누락될 수 있다. 기존 경로가 있으면 덮어쓰지 않는다.

```sh
git clone https://github.com/OREUM-IT/jeju-tour-data-analysis.git jeju-tour-data-analysis
cd jeju-tour-data-analysis
git switch <실제-전체-작업-PR-head-브랜치>
git rev-parse HEAD
```

## 4. 무엇이 재사용되고 무엇이 사라질 수 있는가

| 위치 | 재개 시 취급 |
|---|---|
| Git에 포함된 `docs/`, `data/`, `design/`, 선택된 `reports/`, `scripts/`, 서비스·예제·워크플로 | 최종 PR/커밋에 실제 포함된 파일이 재사용 기준. 단순히 현재 디스크에 존재하는 것과 구분. 원본 CSV ZIP은 제외 |
| `reports/jeju-data-snapshot-bundle.zip` | 라이선스 검토 전 로컬 재생성 산출물. 공개 PR/배포 첨부에 포함하지 않음. 빌더·카탈로그·출처 기록으로 재현 |
| `.mcp.json`, `.codex/config.toml` | 토큰 없는 프로젝트 설정. 클라이언트 승인·프로젝트 신뢰·네트워크 적용은 새 환경에서 별도 |
| `services/jeju-mcp/node_modules` | 커밋하지 않음. 잠금파일로 `npm ci` 재생성 |
| `services/jeju-mcp/.local/catalog.json` | 로컬 가져오기 결과. Git에 없음. 고정 원본을 확보해 재생성 |
| 서비스·Gateway의 `.env.local`, 토큰, OAuth 캐시 | 비밀 설정. 이관용 문서나 ZIP에 넣지 않고 승인된 비밀 저장소/클라이언트로 다시 공급 |
| `/workspace/scratch/reference-repos/` | 현재 환경에 있는 읽기 전용 참고 clone. 새 환경에서는 존재를 보장하지 않으므로 고정 커밋으로 재확보 |
| `/workspace/scratch/jeju-research/`, npm 캐시, 설치된 보고서 도구 | 조사·빌드 편의 자산. 코드의 필수 외부 입력으로 의존하지 않음 |
| `/tmp`의 검사 결과·빌드 임시 파일·OAuth callback 과정 | 일시적. 경로·프로세스·세션을 인수인계된 것으로 가정하지 않음 |

`/workspace` 경로도 다른 컴퓨터에서 자동 재현되지 않는다. Windows/macOS에서는 작업 경로를 맞추고 입력 옵션을 명시한다. 기본 경로가 현재 클라우드를 가리키는 스크립트에는 아래의 `--source-repo`, `--source`, `--input`을 사용한다. 캐시 문제는 작업 전용 경로로 해결하고 `HOME`·`CODEX_HOME`을 바꾸거나 보호된 전역 설정을 편집하지 않는다.

## 5. 참고 저장소를 고정 커밋으로 다시 확보

| 저장소 | 고정 커밋 | 역할 |
|---|---|---|
| `OREUM-IT/jeju-tour` | `b0602a175c1d08959b7187da2b29ac450b52e0ba` | 원본 웹·서버·설문·CSV·장소 JSON |
| `OREUM-IT/jeju-tour-flutter` | `81e5f2acc1b5fd947b183fe1d74b6707717518e7` | 관련 앱·원본 서비스 연결 관계 참고 |

다음은 대상 폴더가 없는 새 환경에서만 실행한다. 기존 인증이 제공된 Git 연결을 사용하며 토큰을 clone URL에 넣지 않는다. 접근 거부는 GitHub 권한·네트워크 전제로 기록하고 다른 경로로 우회하지 않는다.

```sh
JEJU_REFERENCE_ROOT=/workspace/scratch/reference-repos
mkdir -p "$JEJU_REFERENCE_ROOT"
git clone --no-checkout https://github.com/OREUM-IT/jeju-tour.git "$JEJU_REFERENCE_ROOT/jeju-tour"
git -C "$JEJU_REFERENCE_ROOT/jeju-tour" checkout --detach b0602a175c1d08959b7187da2b29ac450b52e0ba
git clone --no-checkout https://github.com/OREUM-IT/jeju-tour-flutter.git "$JEJU_REFERENCE_ROOT/jeju-tour-flutter"
git -C "$JEJU_REFERENCE_ROOT/jeju-tour-flutter" checkout --detach 81e5f2acc1b5fd947b183fe1d74b6707717518e7
git -C "$JEJU_REFERENCE_ROOT/jeju-tour" rev-parse HEAD
git -C "$JEJU_REFERENCE_ROOT/jeju-tour" status --porcelain
git -C "$JEJU_REFERENCE_ROOT/jeju-tour-flutter" rev-parse HEAD
git -C "$JEJU_REFERENCE_ROOT/jeju-tour-flutter" status --porcelain
```

현재 두 clone은 위 SHA이며 clean 상태임을 확인했다. 재개 후에도 HEAD 일치와 변경 없음을 확인한다. 참고 저장소에서 설치·생성물 출력·소스 편집·push·삭제를 하지 않는다. 새 구현은 분석 저장소의 작업 브랜치에서 진행한다.

## 6. 로컬 MCP의 최소 재개 절차

### 의존성과 필요한 검사

아래는 분석 저장소 루트 기준이다. 설치된 Node는 이번 클라우드에서 `24.22.1`, Python은 `3.12.14`였다. 새 환경의 버전을 확인하고 잠금 의존성을 사용한다.

```sh
node --version
python3 --version
npm --prefix services/jeju-mcp ci --ignore-scripts --no-audit --no-fund
```

새 환경의 검증 또는 관련 코드 변경이 있을 때만 다음 기존 검사를 실행한다. 문서만 수정했고 같은 코드·환경에서 성공 결과가 있으면 반복할 필요가 없다. 테스트 확대나 모델 다운로드는 인수인계 자체의 전제가 아니다.

```sh
npm --prefix services/jeju-mcp test
node --test examples/ollama-tunnel/gateway.test.mjs
```

현재 기대값은 32 + 10 = 42개다. HF 8개는 32개에 포함되므로 따로 더하지 않는다. npm 캐시가 쓰기 불가한 경우 작업용 `--cache /workspace/scratch/npm-cache`를 추가한다. SDK `1.32.1`, zod `4.6.7`, MCP 프로토콜 `2025-11-25` 기준을 유지한다. v2 업그레이드는 별도 변경이다.

### 장소 가져오기와 서버 시작

`services/jeju-mcp/.local/catalog.json`이 없는 경우에만 아래 가져오기를 실행한다. 변환기는 기존 출력을 덮어쓰지 않으며 입력 Git·파일 일치를 검사한다.

```sh
node services/jeju-mcp/scripts/import-catalog.mjs \
  --input /workspace/scratch/reference-repos/jeju-tour/prototype/data/jeju_tour_data.json \
  --output services/jeju-mcp/.local/catalog.json
```

기존 결과가 있으면 재가져오려고 지우지 말고 출처·모드·건수를 확인한다. 정상 고정 원본에서는 `dataMode:reference-import`, 7,561건이다. 접근성·실내·비용은 원본 분류로 추론해 채우지 않는다. 필드의 유효 근거가 없으면 `unknown`을 유지한다.

다음 설정 생성은 해당 서비스 폴더에 `.env.local`이 **없는 경우만** 실행한다. 기존 파일은 출력하거나 덮어쓰지 않는다. 기존 환경에서는 운영자가 `MCP_CATALOG_PATH`가 원하는 카탈로그를 가리키는지만 비밀값 노출 없이 확인한다.

```sh
cd services/jeju-mcp
node scripts/configure-local.mjs --catalog .local/catalog.json
node --env-file=.env.local src/server.mjs
```

서버는 기본 `127.0.0.1:8788`이다. 다른 터미널에서 같은 서비스 폴더로 이동해 실제 SDK 검사를 수행한다.

```sh
node --env-file=.env.local scripts/check-client.mjs
```

완료 기준은 초기화·도구 3개 발견·세 도구 호출, 실제 자료 모드와 버전 확인이다. fixture 성공을 실제 카탈로그 성공으로 기록하지 않는다. `/healthz`는 생존 확인이고 인증된 `/readyz`는 모드·건수 확인이다. 로컬 시작만으로 공개 배포가 완료되지는 않는다.

## 7. 산출물 재생성은 필요한 경우만

현재 파일을 읽는 데 재생성은 필요 없다. 원본이나 템플릿을 바꿨을 때 분석 저장소 루트에서 실행하고 결과 diff를 검토한다. 조사 시각이 달라지면 메타데이터와 ZIP 해시도 달라질 수 있다.

```sh
python3 scripts/build_raw_data_inventory.py --source-repo /workspace/scratch/reference-repos/jeju-tour
python3 scripts/build_data_source_catalog.py --source /workspace/scratch/reference-repos/jeju-tour
python3 scripts/build_persona_assets.py
python3 scripts/build_deployment_diagrams.py
python3 scripts/build_tunnel_bundle.py
python3 scripts/build_report.py --pdf
```

앞의 데이터 두 명령과 ZIP 생성기는 표준 Python을 사용한다. 페르소나 그림에는 `numpy`·`matplotlib`, 배포 그림에는 Graphviz `dot`, Word/PDF에는 `python-docx`·`pandoc`·`soffice`와 한글 글꼴이 필요하다. 새 환경에 없으면 필요한 산출물의 의존성만 설치하고 서비스 잠금파일과 섞지 않는다. `python3 -m pip`를 사용하며, 현재 도구 설치 상태를 새 컴퓨터의 보장으로 쓰지 않는다.

`build_data_source_catalog.py`의 기본 동작은 카탈로그 갱신이며 원본 CSV ZIP을 만들지 않는다. 필요한 로컬 보존본을 재생성할 때만 `--bundle`을 명시한다.

```sh
python3 scripts/build_data_source_catalog.py --source /workspace/scratch/reference-repos/jeju-tour --bundle
```

출력 `reports/jeju-data-snapshot-bundle.zip`은 원래 CSV 바이트와 출처를 포함하는 **로컬 재생성 경로**이며 공개 PR에서 제외한다. 라이선스 공란 8개와 개별 조건 미확인 2개를 비롯한 재배포 조건을 해결하기 전에는 공개 첨부나 HF 업로드로 확대하지 않는다. 빌더가 ZIP을 만들었다는 사실은 재배포 승인이 아니다. 별도 원 Excel이나 누락된 파생 통계의 원천 26 CSV가 생겼다고 가정하지 않는다. 검사 스크립트는 원본을 수정하지 않는다.

## 8. 다음 작업 순서와 완료 기준

추가(2026-11-18): Agent 구현은 [19 문서](19-agent-step2-data-foundation.md)의 단계적 절차를 따른다. 1·2단계(진단·근거 등급 카탈로그)는 완료했고 다음은 3단계 평가셋이다. 아래 연결·계정 작업은 그와 병행할 수 있다.

| 순서 | 할 일 | 완료 기준 |
|---|---|---|
| 1 | 전체 작업 PR·head·본문·검사 결과 보존 | 설계·카탈로그·보고서·서비스·인수인계가 의도한 PR에 포함되고 비밀/캐시/원본 CSV ZIP이 제외됨. 성공한 CI와 미검증 항목을 본문에 구분 |
| 2 | 실제 실행 환경의 HF 네트워크 허용과 클라이언트 기능 확인 | draft 저장과 런타임 적용을 구분. 허용 정책 적용 후 한 번의 실제 `list`가 성공. 현재 대화/Claude Code에서 도구가 실제 발견됨 |
| 3 | 새 HF 계정으로 무료 `oreumit` 조직 생성·연결 | 실제 계정명, 생성된 URL, Members의 admin 확인. 이미 같은 이름이 있으면 소유권 확인. 다른 계정으로 임의 생성하지 않음 |
| 4 | HF 읽기 도구 실제 호출과 수동 연결 CI | 공개 목록·모델/데이터셋 검색 성공. 조직 권한 확인과 별도로 기록. main의 수동 smoke 실행 URL·head SHA·결과 보존 |
| 5 | 논문·PDF·Excel 원본 확보 | 파일 해시·출처와 원문 본문 확인. LAG 정의·절차·평가를 페이지/절 단위로 정리. Excel의 시트·원래 행 번호를 55개 파일과 매핑 |
| 6 | 데이터 정제·근거 어댑터 | P0 장소 ID·좌표·중복, 상세 연결의 모호성, 사용 조건·필드 유효기간 검토. 조건 검사의 unknown을 근거 없이 pass로 바꾸지 않음 |
| 7 | 사용자 장비의 실제 모델 검증 | 정확한 태그/digest·라이선스·RAM/VRAM·양자화·한국어·JSON·도구 왕복·지연 확인. 모델 레지스트리에 관측 결과 기록 |
| 8 | 보호된 호스트·Preview 통합 설계 구체화 | DNS·TLS·Access·서비스 인증과 상시 호스트 확인. 기존 서비스 계약을 보존한 별도 Preview/변경안. 운영 배포는 아직 실행 대상이 아님 |

2~4를 우선 진행하되, 외부 접근이 막히면 이미 가진 데이터의 정제 설계와 원문 확보 준비를 병행할 수 있다. 사용자에게 이미 승인된 전체 작업을 다시 승인받기 위해 멈추지 않는다. 실제 도구·권한·자료가 없을 때만 누락된 전제와 필요한 최소 화면 작업을 구체적으로 전달한다.

HF 공개 호출 명령은 다음과 같다. 프록시 설정이 제공된 Node 24 환경에서 사용한다.

```sh
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs list
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs models "Korean instruction"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs datasets "Jeju tourism"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs paper 2504.09277
```

공개 조회에는 HF_TOKEN을 필수로 요구하지 않는다. 개인화/비공개 접근은 새 계정의 OAuth 또는 필요한 저장소 읽기로 제한한 fine-grained 토큰을 보안 설정으로 제공받는다. MCP 설정의 도구 노출과 실제 호출 권한은 다르므로 클라이언트 허용 목록도 유지한다. `paper.md` 응답은 본문이 아니라 초록·AI 요약 fallback일 수 있다. 성공 상태만 보고 논문 읽기 완료로 표시하지 않는다.

수동 HF 검사는 GitHub Actions의 **Hugging Face MCP anonymous smoke → Run workflow → main**에서 실행한다. 이 워크플로는 인증 Secret·HF 업로드·추론을 사용하지 않는다. 현재 성공한 42개 CI와 다른 워크플로이므로 실행 결과를 섞지 않는다.

## 9. 이미 승인된 범위와 접근 경계

사용자는 클라우드 설정, 기획·시각화·보고서·데이터 정리, 실행 가능한 읽기 전용 MCP, HF 새 계정의 조직 생성·MCP 연결·CI, 전체 작업 PR 및 Claude Code 인수인계를 요청했다. 같은 범위의 가역적인 구현·검증·PR 작업에 포괄적인 재확인을 요구하지 않는다. 브라우저 도구가 제공되면 이미 승인된 계정 화면 작업을 이어갈 수 있지만, 올바른 계정·대상 리소스를 먼저 확인해야 한다.

현재 세션에는 브라우저 제어, 임의 HF connector 등록, 실행 환경 네트워크 apply/Publish 도구가 없었다. 환경 상태·진단 조회와 draft 저장은 가능했다. 새 세션에서 도구를 다시 확인하되, 설정 파일 작성이나 웹 브라우저 로그인만으로 도구 권한을 얻었다고 주장하지 않는다. 보호 경로의 전역 설정·프로젝트 신뢰를 바꾸어 자동 활성화를 강제하지 않는다.

무료 조직·읽기 전용 조회·기존 CI 범위를 유지한다. Team/Enterprise 구독, 유료 GPU/추론/Jobs, 멤버 초대, 실제 HF 업로드, 원본 앱 배포, DNS 레코드 변경, DB migration, 사용자 코스 수정·삭제를 이번 인수인계에서 자동 실행하지 않는다. 특히 외부 reference clone에는 push하지 않는다.

비밀값은 `.env.local`, 서버/CI 보안 설정, 클라이언트 OAuth 저장소에 둔다. 토큰·쿠키·OAuth 응답·Access secret을 출력하거나 URL, 셸 기록, PR, ZIP에 넣지 않는다. 필요한 때도 채팅에 비밀을 붙여 달라고 요청하지 않는다. HF 조직과 GitHub 조직은 서로 다른 권한 체계이며 브라우저 로그인·Git 인증·GitHub API·MCP OAuth도 별도다.

공개 저장소에 파일이 있다는 사실과 재배포·외부 서비스 업로드·모델 학습·상업 사용 권리는 다르다. 기존 JSON에는 지도/OSM/큐레이션 자료가 혼합돼 있고 CSV에도 제공기관별 조건이 있다. 원 출처·라이선스·수집 방식·개인정보 여부를 필드/파일별로 검토하고 신규 외부 업로드에 무검증 자료를 확장하지 않는다. 실제 사용자 응답·행동 데이터는 아직 없으며, 이후 수집은 동의·최소 수집·보존/삭제 정책과 함께 설계한다.

## 10. 막혔을 때의 정확한 진단

| 관측 | 의미와 다음 전제 |
|---|---|
| `CONNECT tunnel failed, response 403` | 현재 실행 환경의 프록시/도메인 정책 단계에서 거부. HF 비밀번호·토큰 오류나 서비스 장애로 단정하지 않음. 허용 draft의 실제 적용이 필요 |
| 환경 draft에는 HF 도메인이 있고 런타임에는 없음 | 초안과 실행 정책의 차이. 사용자 환경 설정 저장·게시 또는 제공된 관리 도구가 필요. 없는 apply 도구를 실행했다고 말하지 않음 |
| 사용자 브라우저에는 HF 로그인, 에이전트에는 브라우저 도구 없음 | 새 계정의 세션을 읽거나 조작할 수 없음. 브라우저 기능 제공 또는 조직 생성·OAuth 승인 같은 최소 화면 작업이 필요. 쿠키 추출로 옮기지 않음 |
| `codex mcp get huggingface`가 없음 | 프로젝트 설정 자동 로딩/신뢰/클라이언트 적용 미확인. 일회성 `-c` 파싱은 확인했지만 활성 연결 증거가 아님. Claude Code의 프로젝트 승인도 별도 |
| 실제 HF 응답이 401/403 | 네트워크 CONNECT 오류와 구분. 운영 서버 인증 요구·OAuth scope·조직 권한을 확인. 모든 권한의 새 토큰 발급부터 요구하지 않음 |
| `tools/list`가 partial 또는 기대 도구 누락 | 연결만 성공했을 수 있음. 실제 스키마·preset·클라이언트 허용 목록을 비교. 현재 smoke의 실패 조건을 통과로 완화하지 않음 |
| Git push 가능, GitHub API 불가 | Git과 REST API 접근은 별개. PR 조회/생성/dispatch가 blocked일 수 있으며 Git push만으로 PR이 생성됐다고 기록하지 않음 |
| `.local` 출력 또는 `.env.local`이 이미 있음 | 덮어쓰기 방지가 정상 작동. 파일을 지우거나 토큰을 재생성하지 않고 기존 산출물의 목적·출처를 확인 |
| 모델 CLI·GPU·11434가 없음 | 해당 클라우드에서 미실행. 사용자 Windows/Mac에서 모델 실행이 불가능하다는 근거는 아님 |
| 도메인 조회가 타임아웃/403 | 도메인 미등록·NS 미설정·TLS 실패를 뜻하지 않음. 사용자 보고와 실제 권한 있는 조회를 분리 |

차단이 유지되는 동안 같은 네트워크 호출·CI를 반복해 성공을 기대하지 않는다. 설정 적용, 새 도구, 계정 권한, 새 원자료처럼 전제가 바뀐 뒤 제한된 검사를 재개한다. 마지막 결과에는 완료한 변경, 실제 검증, 아직 막힌 작업과 그 정확한 전제를 각각 남긴다.
