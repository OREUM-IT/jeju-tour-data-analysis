# Claude Code 인수인계와 작업 재개 절차

기준일: 2026-11-19 (한국 시간, Codex 기준선 하네스 구현 반영). 목표는 기존 Vercel 제주 관광 앱을 활용하는 개인화 Agent를 [19 문서](19-agent-step2-data-foundation.md)의 단계로 구현하는 것이다. 이 문서는 다음 세션이 그대로 이어받을 상태, 재개 절차, 다음 작업 명세를 담는다. 0절이 최신 상태다. 1절 이하는 이전 세션의 기록과 절차를 포함한다. 과거 상태보다 확인 시점이 최근인 근거를 우선하고, 새 결과가 생기면 근거와 함께 갱신한다.

## 0. 지금 이어받을 상태 (2026-11-19)

**최신 구현:** PR #7 병합 커밋 `main@6ade0c3`에서 `codex/agent-live-baselines`를 만들었다. [24 문서](24-agent-baseline-scoring.md)의 오프라인 하네스를 재사용하는 [실제 수집기](25-live-baseline-execution.md), 중단 재개, 잠정 모델 판정과 [Vertex AI 인증 흐름](26-langflow-vertex-auth.md)을 추가했다. 사용자가 실제 Gemini·Langflow 실행과 필요한 Google 인증 사용을 승인했다. 같은 `gemini-3.8-flash`의 실제 응답과 Langflow MCP 도구 호출을 확인했으며 결과·실패·한계는 25 문서에 기록한다. 자유 문장의 모델 판정은 잠정이며 사람 24문항 검수는 남아 있다. 이전 작성 질의와 합성 보안 사례 14개 실행은 여전히 미완료다. 정답 해시 `b9821945…`와 템플릿 96문항은 유지한다. [자체 검사 집계](../reports/baseline-harness-self-check.json)는 실제 모델 평가와 별도의 oracle 검사다.

**이후 추가된 운영·보안 설계:** `main@725737f`를 기준으로 [21 Linux Docker](21-linux-docker-production.md)와 [22 탈옥 방어](22-jailbreak-and-prompt-injection-defense.md)를 추가했다. 사용자 지정 논문 `2403.17336`은 저자 공식 GitHub README로 서지·공격 평가 연구임을 확인했으며 PDF 본문은 403으로 미확보다. [합성 보안 평가 14개](../data/agent-security-cases.json)는 아직 실행하지 않았다. Gateway·최종 답변 검증기·벡터 DB·운영 컨테이너는 미구현이다. 현재 로컬 구성은 유지하며 3단계 평가셋에 정상·공격 사례와 실제 부작용 계측을 함께 설계한다. 기존 설문 v0.3 등 `codex/ai-agent-model-handoff`의 추가 커밋은 이 새 브랜치에 자동 병합하지 않았다.

**3단계 평가셋과의 관계:** [23 문서](23-agent-step3-evaluation-set.md)의 평가셋은 정상 요청 96문항과 결정론적 채점 함수까지다. 위 합성 보안 사례 14개를 같은 하네스로 실행하는 일과 실제 부작용 계측은 하지 않았고, 11절의 다음 작업에 넣었다.

| 항목 | 상태 |
|---|---|
| main | 작업 시작 시 `6ade0c3`(PR #7 병합). 실제 수집·Vertex AI 연결은 새 브랜치 `codex/agent-live-baselines`의 PR로 전달한다. 병합은 사용자가 한다 |
| 단계 | 1 진단 ✅ · 2 데이터 기반 ✅ · 3 평가셋 ✅ · 오프라인 하네스 ✅ · 실제 Gemini·Langflow 호출 ✅. 실측 집계는 25 문서, 사람 검수·MCP v2 연결·보안 평가는 후속 작업 |
| 검사 | 로컬 Node 24.22.1: MCP·HF·Agent 카탈로그·평가셋·수집·채점 147개 + Gateway 10개 = 157개 통과. `check_repository.py` 12개 항목 PASS. 실제 96문항 자체 검사와 저장된 실행 재채점 `--check`도 확인. CI 결과는 이번 PR에서 확인 |
| 데이터 실측 | 원본 앱 장소 981곳(원시 POI 863·앱 생성 118). `verified` 0, 비용 0, 실내 출처 132, 영업시간 출처 466, 동네 산책 테마 대표 후보 7. 집계는 [`reports/agent-catalog-coverage.json`](../reports/agent-catalog-coverage.json) |
| 평가셋 실측 | 96문항(페르소나 8 × 슬롯 12). 추천 48·확인 불가 32·되묻기 8·불가 안내 8, 필수 어려운 사례 5종 각 8문항, 필수 조건 판정은 모두 `unknown`. 작성 질의 96건과 템플릿 문장 96건이 문장 규칙 9개(200자 상한, 조건 반영, 명시하지 않은 조건·숫자 금지, 후보 이름 금지 등)를 통과했다. 규칙은 낱말 목록에 기대는 검사라 놓치는 경우가 있다(23 문서 10절). 집계는 [`reports/evaluation-set-summary.json`](../reports/evaluation-set-summary.json) |
| Langflow | 1.13.3 로컬에서 Vertex AI 서비스 계정 OAuth, `gemini-3.8-flash` 실제 추론·MCP 도구 호출·구조화 결과를 확인했다. Desktop·Docker·사용자 PC·운영 배포는 미검증 |
| 로컬 산출물 | 현재 `.local`에 `app-places-b0602a1.json`, `agent-catalog-b0602a1.json`, `evaluation-set-b0602a1.json`, `evaluation-briefs-b0602a1.json`, `baseline-plan-b0602a1.json`, `baseline-oracle-run-b0602a1.json`, `baseline-oracle-scores-b0602a1.json`이 있다. 다른 환경에서는 [19](19-agent-step2-data-foundation.md)·[23](23-agent-step3-evaluation-set.md)·[24](24-agent-baseline-scoring.md) 문서로 재생성한다. 이전 작성 질의는 복원하지 않았다. 현재 런타임의 비밀 설정·Langflow DB·원문 캡처·판정은 Git 밖의 로컬 자료이며 다음 환경에 자동 이관되지 않는다 |

**사용자 결정 (2026-11-18)**

- 논문: `2504.09277`은 SynthTRIPs로 평가셋 생성에, LAG는 `2508.05509`로 추론 비교 실험에 쓴다(둘 다 사용).
- SynthTRIPs는 저자 공식 GitHub 코드 `ashmibanerjee/synthTRIPS-code`를 참고해 설정한다. 커밋 `9562f53`의 파일 52개를 읽어 평가셋 명세에 반영했다([23 문서](23-agent-step3-evaluation-set.md) 2절). 코드 실행·재현은 하지 않았다.
- 결과물 전달: 분석 저장소에 새 브랜치 push + PR. 병합은 사용자가 한다. 이번 세션의 PR은 사용자가 바로 병합했다.
- 시각적 Agent 빌더: Langflow(1.13.0 이상, 로컬). Flowise는 지원 종료로 제외했다.
- 시각화 커넥터(Mermaid Chart·Figma·Metabase·Grafana Cloud)는 안내만 했고, 사용자가 아직 고르지 않았다.

**추가 사용자 결정과 남은 입력**

- 2026-11-19: 실제 Gemini·Langflow 평가를 진행하도록 승인했고 Vercel 비밀 설정의 Google 인증을 사용했다. 같은 범위의 실행을 위해 포괄 승인을 다시 묻지 않는다. Langflow를 사용자 PC로 옮길 경우 OS는 확인해야 한다.
- 평가셋 정답 후보(장소 ID·이름 포함)를 Git에 올려도 되는지. 답이 없으면 `.local`에 두고 집계만 올린다. 정답 후보·원문 공개는 승인받지 않았으므로 계속 `.local`에 둔다. API 전송 승인은 위 실제 평가 범위와 구분한다.

## 1. 바로 이어받을 상태

| 항목 | 확인된 결과 | 아직 확인하지 않은 것 |
|---|---|---|
| 분석 저장소 | `OREUM-IT/jeju-tour-data-analysis` main `b1072a3`(2026-11-18, PR #5 병합). Codex 단계 기준 커밋은 `ad3c36384117696457d4c6ee547d9659629116fa` | 이후 커밋과 열린 PR은 GitHub에서 확인 |
| CI | Codex 단계 [실행](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44226883163) 42개에서 Claude 세션 이후 51개(MCP·HF·Agent 카탈로그 41 + Gateway 10)로 늘었다. PR #3 [실행](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44265260167) 통과 | 실제 HF 운영 서버 연결이나 모델 추론 성공은 이 결과에 포함되지 않음 |
| 전체 작업 | PR #1(`codex/ai-agent-model-handoff`)과 PR #2·#3(`claude/agent-step2-data-foundation`)이 병합됐다 | 열린 PR은 GitHub REST로 확인(3절) |
| 페르소나 | 9개 테마 + 4개 연속 선호 축, 총 13축의 수작업 프로토타입 8개; 독립 HTML 탐색기 | 실제 관광객 표본·학습·경험적 군집·추천 효과 검증 없음 |
| CSV | 55파일, 데이터 논리행 합계 210,532, 컬럼 기록 675; 원본 스냅샷 37·제주 추출본 17·파생 집계 1 | 라이선스 공란 8개·개별 조건 미확인 2개가 있어 원본 CSV ZIP은 공개 PR에서 제외. 최신성·중복·전체 Excel 대조도 미완료 |
| 제주 자체 MCP | `places.search`, `places.get`, `constraints.check`; 기존 JSON의 POI 7,561건 로컬 가져오기와 실제 SDK 통신 검사. Langflow 1.13.3의 MCP 클라이언트로도 호출 확인 | 공개 서버 배포·Vercel 연결·개인별 OAuth·추천 Agent 미완료. 도구는 아직 v1 카탈로그(제약 값 0)를 쓴다. 카탈로그 v2 연결은 4단계 |
| HF MCP | 프로젝트 설정과 독립 클라이언트, 신규 HF 모의 검사 8개가 위 32개에 포함 | 실제 공개 접속은 현재 클라우드 프록시 403; 연결 성공 미확인 |
| HF 계정·조직 | 사용자가 다른 계정으로 로그인했다고 보고. 기본 조직 ID 제안은 `oreumit` | 실제 사용자명·조직 ID 가용성·관리자 권한 미확인, 조직 생성 미실행 |
| 도메인 | 사용자 보고: `oreumit.com`의 Vercel Valid Configuration, Cloudflare NS `dilbert.ns.cloudflare.com`·`irma.ns.cloudflare.com` 저장 | 계정 화면 직접 조회, 실제 DNS Active·전파·TLS·웹 alias 목록 미확인 |
| 모델 | 기존 앱 코드의 공급자 경로와 후보 모델을 문서화. Langflow 시작 흐름의 Agent 노드는 모델·키가 비어 있다 | 이 작업에서 Ollama 적재·LLM 추론·성능·비용 실측 없음 |
| 연구 원자료 | `2504.09277` = SynthTRIPs(평가셋 생성 틀), LAG = `2508.05509`(2026-11-18 사용자 결정, [19 문서](19-agent-step2-data-foundation.md)). SynthTRIPs는 저자 코드 저장소를 커밋 `9562f53`으로 읽어 대조했다([23 문서](23-agent-step3-evaluation-set.md) 2절) | 두 논문 본문 세부, SynthTRIPs의 Hugging Face 데이터셋(지식베이스, 질의 생성·판정·벤치마크 프롬프트 템플릿), 별도 PDF·목록 Excel 1,259항목의 원본 미확보 |

55개 CSV는 7,561건 MCP 카탈로그와 다른 산출물이다. CSV 전체를 MCP에 적재한 상태가 아니다. 210,532는 고유 장소 수가 아니며 파생 수요 통계도 관광객 선호 정답이 아니다. `/readyz`의 `productionReady:false`는 실제 자료 모드에서도 유지한다.

## 2. 먼저 읽을 파일

1. [README](../README.md)와 이 문서 0절, [19 문서](19-agent-step2-data-foundation.md), [23 문서](23-agent-step3-evaluation-set.md), [20 문서](20-langflow-agent-builder.md): 전체 산출물·현재 상태·Agent 단계·평가셋·Langflow.
2. [모델 레지스트리](17-ai-agent-model-registry.md), [기계 판독 모델 목록](../data/ai-agent-models.json): 기존 코드 기본값, 새 후보, 실행 미검증 상태. 이 목록은 자동 라우터가 아니다.
3. [HF MCP 설정](15-huggingface-mcp-setup.md), [HF 조직·CI](16-huggingface-organization-and-ci.md): 현재 가장 먼저 이어갈 연결 작업.
4. [기존 서비스 검토](05-existing-service-review.md), [MCP·Vercel 통합](10-mcp-server-and-vercel-integration.md): 실제 원본 계약과 제안의 경계.
5. [확보 CSV](14-confirmed-data-catalog.md), [데이터 계획](02-data-plan.md), [미확인 근거](06-evidence-and-open-items.md): 원본·권리·시점·조인 문제.
6. [페르소나 설계](08-persona-clustering.md), [Ollama·LAG 계획](09-ollama-lag-feasibility.md), [다중 공급자·DB](11-multi-provider-tunnels-database-and-cost.md).
7. [도메인 절차](12-domain-tunnel-and-ollama-runbook.md), [후속 배치와 MCP 상태](13-next-steps-and-mcp-implementation.md), [서버 실행 안내](../services/jeju-mcp/README.md).

현재 Word/PDF는 이전 문서 01~23의 스냅샷이다. 이번에는 실행 코드·Markdown·JSON 집계를 갱신했고 바이너리 보고서는 재생성하지 않았다. 최신 상태는 24 문서·이 인수인계·실제 CI·코드에서 읽는다.

## 3. Git와 PR 작업 방식

새 세션은 최신 main에서 시작한다. 기존 체크아웃이 있으면 새 clone 전에 상태를 확인하고, 사용자의 미커밋 작업을 보존한다. 이전 기준 SHA로 강제 reset하지 않는다.

```sh
git clone https://github.com/OREUM-IT/jeju-tour-data-analysis.git   # 체크아웃이 없을 때만
cd jeju-tour-data-analysis
git status --short
git log -3 --oneline
git fetch origin main
git switch -c claude/<작업-이름> origin/main
```

- 결과는 새 브랜치 push + PR로 올린다(사용자 결정). main에 직접 push하거나 병합·강제 push하지 않는다. 병합은 사용자가 한다.
- 같은 브랜치에 커밋을 더하기 전에 그 PR이 이미 병합·종료됐는지 확인한다. 이번 세션에서는 PR #2가 병합된 뒤 같은 브랜치에 올린 커밋이 PR #2에 반영되지 않았고, 사용자가 PR #3으로 따로 병합했다. 병합됐다면 최신 main에서 새 브랜치를 만든다.
- Claude Code 클라우드 세션에서는 GitHub GraphQL이 막혀 `gh pr create`·`gh pr view`가 403으로 실패한다. REST로 조회·생성·수정한다.

```sh
gh api "repos/OREUM-IT/jeju-tour-data-analysis/pulls?state=open" --jq '.[] | {number, title, head: .head.ref}'
gh api repos/OREUM-IT/jeju-tour-data-analysis/pulls/<번호> --jq '{state, merged, head: .head.sha}'
gh api -X POST repos/OREUM-IT/jeju-tour-data-analysis/pulls \
  -f title='<제목>' -f head='claude/<작업-이름>' -f base=main -F body=@<본문 파일>
gh api -X PATCH repos/OREUM-IT/jeju-tour-data-analysis/pulls/<번호> -F body=@<본문 파일>
```

- CI는 `main`·`codex/**` push와 pull_request에서만 돈다. PR 동기화가 늦거나 PR 전에 확인하려면 `gh api -X POST repos/OREUM-IT/jeju-tour-data-analysis/actions/workflows/ci.yml/dispatches -f ref=<브랜치>`로 한 번 실행하고, 결과 URL을 PR에 남긴다.
- PR 본문에는 범위, 실측 검증, 미검증 항목을 나눠 적는다. 본문을 고치기 전 현재 내용을 파일로 저장하고 수정 후 대조한다. 이미 있는 설명을 짧은 새 요약으로 덮어쓰지 않고, 병합된 PR에는 검증 기록만 덧붙인다.

## 4. 무엇이 재사용되고 무엇이 사라질 수 있는가

| 위치 | 재개 시 취급 |
|---|---|
| Git에 포함된 `docs/`, `data/`, `design/`, 선택된 `reports/`, `scripts/`, 서비스·예제·워크플로 | 최종 PR/커밋에 실제 포함된 파일이 재사용 기준. 단순히 현재 디스크에 존재하는 것과 구분. 원본 CSV ZIP은 제외 |
| `reports/jeju-data-snapshot-bundle.zip` | 라이선스 검토 전 로컬 재생성 산출물. 공개 PR/배포 첨부에 포함하지 않음. 빌더·카탈로그·출처 기록으로 재현 |
| `.mcp.json`, `.codex/config.toml` | 토큰 없는 프로젝트 설정. 클라이언트 승인·프로젝트 신뢰·네트워크 적용은 새 환경에서 별도 |
| `services/jeju-mcp/node_modules` | 커밋하지 않음. 잠금파일로 `npm ci` 재생성 |
| `services/jeju-mcp/.local/catalog.json` | 로컬 가져오기 결과. Git에 없음. 고정 원본을 확보해 재생성 |
| `services/jeju-mcp/.local/app-places-*.json`, `agent-catalog-*.json` | 제3자 장소 자료라 Git에 없음. [19 문서](19-agent-step2-data-foundation.md) 5절 순서(참고 앱 사본 빌드 → 덤프 → 내보내기)로 재생성 |
| `services/jeju-mcp/.local/evaluation-set-*.json`, `evaluation-briefs-*.json`, `evaluation-queries-*.json` | 장소 ID·이름이 든 질의·정답 후보와 질의 작성 기록(작성 모델 포함)이라 Git에 없음. [23 문서](23-agent-step3-evaluation-set.md) 8절로 재생성. 정답 해시는 집계 보고서와 대조 |
| `services/langflow/langflow.env`, `.langflow-venv/`, Langflow 설정 폴더(DB) | 비밀번호·암호화 키·전역 변수(토큰)가 들어 있다. Git·이관 대상이 아니다. [20 문서](20-langflow-agent-builder.md) 3·4절로 다시 만든다 |
| 참고 앱 빌드 사본 | `git archive`로 고정 커밋을 별도 폴더에 풀어 빌드한 임시 사본. 참고 checkout에는 설치·빌드하지 않는다 |
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

아래는 분석 저장소 루트 기준이다. 설치된 Node는 Codex 클라우드에서 `24.22.1`, Python은 `3.12.14`였다. Claude 세션 클라우드에서는 Node `22.23.5`, Python `3.13.18`이었고 같은 검사가 통과했다. 새 환경의 버전을 확인하고 잠금 의존성을 사용한다.

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

현재 기대값은 62 + 10 = 72개다(서비스 24·HF 8·Agent 카탈로그 9·평가셋 21 + Gateway 10). 저장소 검사 `python3 scripts/check_repository.py`는 9개 항목이다. npm 캐시가 쓰기 불가한 경우 작업용 `--cache /workspace/scratch/npm-cache`를 추가한다. SDK `1.32.1`, zod `4.6.7`, MCP 프로토콜 `2025-11-25` 기준을 유지한다. v2 업그레이드는 별도 변경이다.

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

### 8.1 Agent 단계 (우선)

| 순서 | 할 일 | 완료 기준 |
|---|---|---|
| 1 | 3단계 평가셋(SynthTRIPs 방식) (완료, [23 문서](23-agent-step3-evaluation-set.md)) | 생성기·테스트·집계 보고서가 PR로 올라가고, 질의마다 근거 사실·조건·기대 동작·정답 규칙이 있다 |
| 2 | 기준선 채점: 오프라인 하네스 완료, 실제 Gemini·Langflow 응답 수집과 비교는 남음([24 문서](24-agent-baseline-scoring.md)) | 같은 계획 해시로 두 시스템의 지표 기록. 실제 호출은 공급자·키·전송 범위 확정 후 |
| 3 | 4단계 도구: 카탈로그 v2 기반 MCP 도구(테마 랭킹, 근거 등급 조건 검사, 영업시간) | `verified`가 아닌 값으로 pass하지 않는 테스트, 기존 3개 도구 호환 유지 |
| 4 | 5단계 Agent 루프와 LAG(`2508.05509`) 비교 | 같은 평가셋에서 허용 ID 위반 0, 필수 조건 오판 0. LAG 원문 세부 확인 전 `method=lag` 표기 금지 |
| 5 | 6단계 비교·연결 | 비교 결과와 되돌리기 절차. 원본 앱 변경·운영 배포는 별도 승인 |

### 8.2 연결·계정·자료 (병행)

| 순서 | 할 일 | 완료 기준 |
|---|---|---|
| 1 | 전체 작업 PR·head·본문·검사 결과 보존 (PR #1~#3 병합으로 완료) | 설계·카탈로그·보고서·서비스·인수인계가 의도한 PR에 포함되고 비밀/캐시/원본 CSV ZIP이 제외됨. 성공한 CI와 미검증 항목을 본문에 구분 |
| 2 | 실제 실행 환경의 HF 네트워크 허용과 클라이언트 기능 확인 | draft 저장과 런타임 적용을 구분. 허용 정책 적용 후 한 번의 실제 `list`가 성공. 현재 대화/Claude Code에서 도구가 실제 발견됨 |
| 3 | 새 HF 계정으로 무료 `oreumit` 조직 생성·연결 | 실제 계정명, 생성된 URL, Members의 admin 확인. 이미 같은 이름이 있으면 소유권 확인. 다른 계정으로 임의 생성하지 않음 |
| 4 | HF 읽기 도구 실제 호출과 수동 연결 CI | 공개 목록·모델/데이터셋 검색 성공. 조직 권한 확인과 별도로 기록. main의 수동 smoke 실행 URL·head SHA·결과 보존 |
| 5 | 논문·PDF·Excel 원본 확보 | 파일 해시·출처와 원문 본문 확인. LAG 정의·절차·평가를 페이지/절 단위로 정리. Excel의 시트·원래 행 번호를 55개 파일과 매핑 |
| 6 | 데이터 정제·근거 어댑터 | P0 장소 ID·좌표·중복, 상세 연결의 모호성, 사용 조건·필드 유효기간 검토. 조건 검사의 unknown을 근거 없이 pass로 바꾸지 않음 |
| 7 | 사용자 장비의 실제 모델 검증 | 정확한 태그/digest·라이선스·RAM/VRAM·양자화·한국어·JSON·도구 왕복·지연 확인. 모델 레지스트리에 관측 결과 기록 |
| 8 | 보호된 호스트·Preview 통합 설계 구체화 | DNS·TLS·Access·서비스 인증과 상시 호스트 확인. 기존 서비스 계약을 보존한 별도 Preview/변경안. 운영 배포는 아직 실행 대상이 아님 |

8.1을 우선한다. 8.2의 2~4는 외부 접근이 열리면 병행하고, 막히면 이미 가진 데이터의 정제 설계와 원문 확보 준비를 병행할 수 있다. 사용자에게 이미 승인된 전체 작업을 다시 승인받기 위해 멈추지 않는다. 실제 도구·권한·자료가 없을 때만 누락된 전제와 필요한 최소 화면 작업을 구체적으로 전달한다.

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

사용자는 클라우드 설정, 기획·시각화·보고서·데이터 정리, 실행 가능한 읽기 전용 MCP, HF 새 계정의 조직 생성·MCP 연결·CI, 전체 작업 PR 및 Claude Code 인수인계를 요청했다. 2026-11-18 Claude 세션에서는 Agent 구축의 단계적 진행, 논문 정정(둘 다 사용), 새 브랜치 push + PR, Langflow 사용을 결정했고 PR 병합은 사용자가 했다. 같은 범위의 가역적인 구현·검증·PR 작업에 포괄적인 재확인을 요구하지 않는다. 모델 공급자 호출(과금·데이터 전송)과 평가 정답 후보의 Git 공개는 아직 승인되지 않았다. 브라우저 도구가 제공되면 이미 승인된 계정 화면 작업을 이어갈 수 있지만, 올바른 계정·대상 리소스를 먼저 확인해야 한다.

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
| `gh pr create`·`gh pr view`가 GraphQL 403 | Claude Code 클라우드 세션 정책이며 인증 문제가 아니다. `gh api` REST로 PR을 조회·생성·수정(3절) |
| push했는데 PR head가 안 바뀜 | 그 PR이 이미 병합·종료됐는지 먼저 확인. 병합됐다면 최신 main에서 새 브랜치·새 PR |
| 브랜치 push 뒤 CI가 돌지 않음 | CI는 main·`codex/**` push와 pull_request에서만 실행된다. `workflow_dispatch`로 한 번 실행 |
| Langflow `SSRFProtectionError`(127.0.0.1) | 로컬 MCP 연결 차단. `LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1`로 다시 시작. 사설망 전체를 열지 않는다 |
| Langflow 전역 변수 저장이 HTTP 500 "Fernet key" | `LANGFLOW_SECRET_KEY` 형식 오류. 줄을 지워 자동 생성하거나 Fernet 형식 키를 넣는다 |
| 참고 앱 서버 시작 시 `public-service-data.json.gz` 없음 | 사본에서 `scripts/copy-migrations.cjs`와 `copy-public-service.cjs`를 먼저 실행([19 문서](19-agent-step2-data-foundation.md) 5절) |
| `arxiv.org`·배포 앱 직접 접속이 403 | 작업 공간 네트워크 허용 목록 밖이다. 웹 조회 도구로 읽고 우회하지 않는다. 두 번째 세션에서는 웹 조회 도구도 DNS 단계에서 실패했고 웹 검색 결과의 초록·요약만 확인했다. 본문이 필요하면 사용자가 환경 설정의 허용 도메인에 `arxiv.org`를 추가한다. `huggingface.co`도 같은 이유로 막혀 SynthTRIPs 데이터셋을 열지 못했다. GitHub의 공개 저장소는 `git clone`으로 받을 수 있어 SynthTRIPs 저자 코드는 그렇게 읽었다 |
| 서브에이전트·워크플로가 "5-hour limit"으로 실패 | 계정 사용량 한도다. 같은 호출을 반복하지 않는다. 한도가 풀릴 때까지 직접 구현·검증으로 진행하고, 독립 검토가 빠졌다는 사실을 결과에 남긴다 |
| 세션이 다른 저장소에서 시작됨 | 저장소 추가 도구로 `OREUM-IT/jeju-tour-data-analysis`(push)와 `OREUM-IT/jeju-tour`(read)를 붙이고 clone한다. `gh api`를 쓸 수 없는 세션에서는 GitHub MCP 도구로 PR·Actions를 조회·생성한다 |
| 평가셋 빌드가 "written for a different brief" | 명세나 카탈로그가 바뀌어 문항의 사실 요약이 달라졌다. 해당 질의를 다시 쓰거나 `--queries` 없이 템플릿 문장으로 만든다 |
| 평가셋 빌드가 "does not mention the stated condition …", "mentions …, which the item does not state", "mentions … that no stated condition accounts for", "names one of the item's candidate places" | 작성 질의가 문장 규칙을 어겼다([23 문서](23-agent-step3-evaluation-set.md) 8.1절). 규칙을 끄지 않는다. 명세의 낱말 목록(`queryAuthoring.cues`)에 있는 말로 조건을 빠짐없이 쓰고, 조건에 없는 요구·숫자(`unstatedWords`의 낱말, 다른 시각·금액·날짜)와 후보 장소 이름을 뺀다. 템플릿 문장이 걸리면 명세의 낱말 목록이 조건 문구와 어긋난 것이다 |

차단이 유지되는 동안 같은 네트워크 호출·CI를 반복해 성공을 기대하지 않는다. 설정 적용, 새 도구, 계정 권한, 새 원자료처럼 전제가 바뀐 뒤 제한된 검사를 재개한다. 마지막 결과에는 완료한 변경, 실제 검증, 아직 막힌 작업과 그 정확한 전제를 각각 남긴다.

## 11. 다음 작업: 사람 검수와 같은 자료에서의 비교

실제 수집·중단 재개·채점 절차와 실행 결과는 [25 문서](25-live-baseline-execution.md), Vertex 인증은 [26 문서](26-langflow-vertex-auth.md)를 따른다. 기존 하네스를 다시 만들거나 실패 문항을 성공으로 바꾸지 않는다.

1. 같은 계획으로 남은 `not_executed` 문항을 이어갈 때 캡처·계측 쌍을 검증한다. 프로세스 중단 중 요청이 전송됐을 수 있으므로 서버 작업과 대조한 뒤 재개한다. 기존 성공·실패를 재호출하지 않는다.
2. 자유 문장의 잠정 모델 판정을 사람이 24문항 표본(페르소나별 3개·슬롯별 2개)으로 대조한다. 실제 사람이 읽기 전에는 `human`으로 바꾸지 않는다. 정답을 보고 응답의 단언을 고치지 않는다.
3. 4단계 MCP 도구를 앱과 같은 근거 등급 카탈로그 v2에 연결한다. 현재 Langflow v1 7,561곳과 평가셋 981곳의 차이를 제거하고, `unknown`을 통과로 바꾸지 않는 계약을 유지한 뒤 새로운 도구 버전으로 비교한다.
4. 이전 작성 질의 파일을 확보하거나 명세대로 새로 작성한 뒤 템플릿과 문체 비교를 진행한다. 현재 템플릿을 작성 질의로 복제하지 않는다.
5. [합성 보안 사례 14개](../data/agent-security-cases.json)의 실행기와 부수 효과 계측을 설계한다. 조건 삭제·unknown 승격·허용 목록 밖 ID는 채점 규칙을 재사용할 수 있지만 권한·비밀·SSRF·비용·렌더링에는 호출 수·canary·감사 이벤트가 필요하다. 정상 응답 수집으로 보안 평가를 완료 처리하지 않는다.

Git에는 코드·테스트·비밀 없는 흐름과 집계만 올린다. 문항·장소 이름/ID·원문·키·Langflow DB는 `.local` 또는 비밀 설정에 둔다. 실제 청구 금액을 확인하기 전 비용은 `null`이다. 원본 앱·운영 배포는 임의로 수정하지 않는다.

## 12. 다음 세션 시작 프롬프트

```text
OREUM-IT/jeju-tour-data-analysis 저장소에서 이어서 작업해 줘.
1) CLAUDE.md와 docs/18의 0절·3절·11절, docs/25·26을 먼저 읽어 줘.
2) codex/agent-live-baselines PR의 병합 여부와 최신 main을 확인해 줘. 병합됐으면 최신 main에서 새 브랜치를 만들어 줘.
3) 기존 수집·채점 하네스와 실제 집계를 재사용해 줘. 실제 Gemini·Langflow 및 Google 인증 사용은 이미 승인했어. 다른 환경에 로컬 파일/비밀이 없으면 필요한 것만 구체적으로 알려 줘. 실패 문항을 조용히 재시도하거나 모델·도구 버전을 섞지 마.
4) 사람 표본 검수와 MCP v2 연결을 다음 단계로 진행해 줘. 키·질의·정답 후보·원문은 Git에 올리지 말고 집계만 공개해 줘.
5) 결과는 새 브랜치 push + PR로 올려 줘. 원본 앱 저장소는 읽기 전용이고 병합은 내가 할게.
```
