# 제주도 맞춤 개인 관광 페르소나(테마) 추천 AI Agent

관광 설문을 여행 선호도와 실행 제약으로 변환하고, 그 근거를 설명하는 개인화 추천 Agent의 기획 저장소입니다. 처음 지정된 논문 **2504.09277은 SynthTRIPs**(관광 추천기 평가용 합성 질의 생성 틀)로 확인되어 평가셋 설계에 쓰고, **LAG는 2508.05509 Logic-Augmented Generation**을 Agent 추론 비교 실험으로 검토합니다(2026-11-18 사용자 결정, [19 문서](docs/19-agent-step2-data-foundation.md)). LAG를 RAG의 오타나 동의어로 해석하지 않습니다.

## 현재 상태

- **카탈로그 v2·최종 검증 구현:** [PR #8](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/8) 병합 후 `codex/agent-catalog-v2-validation`에서 앱과 같은 981곳의 MCP, 원본 요청에 따른 최종 ID·필수 조건 검증, 검증 전 출력을 억제하는 [별도 Langflow 흐름](services/langflow/jeju-tour-agent-vertex-v2.flow.json)을 추가했습니다. 로컬 SDK·모의 모델 검사와 실제 Gemini 실행 요청 1건에서 미확인 실내 조건을 `cannot_confirm`으로 안내하는 동작을 확인했습니다. [27 문서](docs/27-agent-catalog-v2-validation.md)에 검증 범위와 한계를 기록합니다. 새 96문항 품질 평가는 미실행이며 운영 배포는 하지 않았습니다.
- **실제 Gemini·Langflow 수집·채점 완료(2026-11-19):** 같은 계획의 96문항씩을 모두 시도했습니다. [Gemini 집계](reports/baseline-gemini-live-20261119.json)는 지정 모델 응답 92건·실패 4건(혼합 대체 모델 3건·시간 초과 1건), [Langflow 집계](reports/baseline-langflow-live-20261119.json)는 구조화 응답 80건·실패 16건이며 두 실행 모두 미실행 0건입니다. Google Developer API의 503을 진단한 뒤 [Vertex AI 인증](docs/26-langflow-vertex-auth.md)으로 같은 `gemini-3.8-flash`의 Langflow 응답·MCP 도구 호출을 수집했습니다. 결과와 한계는 [25 문서](docs/25-live-baseline-execution.md), 변경은 [PR #8](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/8)에 있습니다. Gemini 자유 문장 판정은 잠정 모델 검수이며 사람 검수는 0/24건, 두 실행의 비용은 미측정입니다. 카탈로그·날짜 문맥이 달라 모델 자체의 우열을 판단하지 않습니다. [채점기 자체 검사](reports/baseline-harness-self-check.json)는 별도의 oracle 검사입니다.
- **운영·보안 설계 추가:** [Linux Docker 전환안](docs/21-linux-docker-production.md)과 [탈옥·프롬프트 주입 방어](docs/22-jailbreak-and-prompt-injection-defense.md)를 정리했습니다. 최종 구조화 응답의 ID·필수 조건 검증은 [27 문서](docs/27-agent-catalog-v2-validation.md) 범위로 구현했지만 전체 도구 권한·비용 통제와 [합성 평가 14개](data/agent-security-cases.json)는 미완료입니다. 현재 자료는 JSON 카탈로그, PostgreSQL + pgvector는 향후 검색 저장소 후보입니다.
- **AI Agent 단계 진행:** [1·2단계 진단과 데이터 기반](docs/19-agent-step2-data-foundation.md)을 마쳤습니다. 원본 앱과 같은 장소 981곳을 속성별 근거 등급과 함께 내보내는 변환기, 페르소나 테마↔앱 분류 [매핑](data/theme-category-map.json), 이름·ID 없는 [커버리지 집계](reports/agent-catalog-coverage.json)를 추가했습니다. `verified` 0건·비용 0건이라 필수 조건은 아직 판정할 수 없습니다. [3단계 평가셋](docs/23-agent-step3-evaluation-set.md)도 만들었습니다. 8개 페르소나 × 12개 슬롯 96문항의 정답을 규칙으로 계산하는 생성기·채점 함수·테스트와 이름·ID 없는 [집계 보고서](reports/evaluation-set-summary.json)가 있고, 질의와 정답 후보는 `.local`에만 둡니다. 카탈로그 v2 연결·최종 구조화 응답 검증·Agent 90초 및 모델 4회 제한은 구현했습니다. 다음은 사람 24문항 검수, 새 흐름의 96문항 품질 평가, 원격 취소·비용 계측 보강과 합성 보안 사례 14개 평가입니다. 작업 명세와 재개 프롬프트는 [인수인계 11·12절](docs/18-claude-code-handoff.md)에 있습니다.
- **시각적 Agent 빌더:** 사용자 결정으로 [Langflow](docs/20-langflow-agent-builder.md)를 씁니다(Flowise는 2026-08-31 지원 종료). Langflow 1.13.3에서 jeju-mcp 도구 3개와 실제 모델 응답을 확인했습니다. [기본 시작 흐름](services/langflow/jeju-tour-agent.flow.json)과 키를 포함하지 않는 [Vertex AI 흐름](services/langflow/jeju-tour-agent-vertex.flow.json)을 제공합니다. 기존 실측의 MCP v1 7,561곳과 앱·평가셋 981곳은 서로 다릅니다. [새 v2 흐름](services/langflow/jeju-tour-agent-vertex-v2.flow.json)은 같은 981곳과 최종 검증을 연결했으며, 이 버전의 전체 96문항 품질 평가는 남아 있습니다.
- **다른 Claude Code 세션의 시작점:** [CLAUDE.md](CLAUDE.md) → [상세 인수인계·재개 절차](docs/18-claude-code-handoff.md) → [전체 AI AGENT MODEL 목록](docs/17-ai-agent-model-registry.md). 모델별 구현·실행·평가 상태는 [JSON 레지스트리](data/ai-agent-models.json)에서도 관리합니다.
- 상세 설계와 읽기 전용 MCP 초기 구현 단계입니다. 장소 검색·상세·조건 검사 3개 도구를 로컬 검증했습니다. 전체 추천 Agent·학습 모델·운영 통합은 후속 작업입니다.
- 기존 서비스: <https://jeju-tour-six.vercel.app/>. 관련 OREUM-IT 저장소의 소스 검토 결과는 아래 문서에 기록합니다.
- 두 논문의 본문 세부, 별도 PDF 및 제주데이터허브 목록 엑셀은 아직 내용 확인을 완료하지 못했습니다. 실제 데이터셋과 논문의 방법·성능을 추정해서 기재하지 않습니다.
- 문항·점수·일정·평가 목표는 검증 전 제안입니다. 기존 서비스에서 확인한 사실과 새 제안을 구분합니다.

## 문서 읽는 순서

1. [제품 목표와 시스템 설계](docs/01-product-and-architecture.md)
2. [기존 서비스·저장소 검토](docs/05-existing-service-review.md)
3. [실제 문항과 응답→추천 점수 설계](docs/04-questionnaire-and-scoring.md)
4. [제주 데이터 확보·활용 계획](docs/02-data-plan.md)
5. [실험·평가·단계별 개발 계획](docs/03-evaluation-and-roadmap.md)
6. [참고 자료와 미확인 항목](docs/06-evidence-and-open-items.md)
7. [구현 절차·상태 전이·API 계약](docs/07-implementation-process.md)
8. [페르소나 유형·군집 설계와 검증](docs/08-persona-clustering.md)
9. [Ollama 기반 LAG 가능성·Vercel 배포 구조](docs/09-ollama-lag-feasibility.md)
10. [MCP 서버 구축·인증·도구·작업·기존 앱 통합](docs/10-mcp-server-and-vercel-integration.md)
11. [Claude·OpenAI 동시 지원·다중 터널·DB 위치·계정과 비용](docs/11-multi-provider-tunnels-database-and-cost.md)
12. [자체 도메인 점검·서비스 선택·관리 MCP·Ollama 연결 안내](docs/12-domain-tunnel-and-ollama-runbook.md)
13. [oreumit.com 네임서버 저장 후 단계·배치도와 MCP 구현 결과](docs/13-next-steps-and-mcp-implementation.md)
14. [실제 확보 CSV 55개·입력 항목·출처·다음 정제](docs/14-confirmed-data-catalog.md)
15. [Hugging Face 공식 MCP 설정·검색 호출](docs/15-huggingface-mcp-setup.md)
16. [Hugging Face 조직·계정·CI 설정과 검증 상태](docs/16-huggingface-organization-and-ci.md)
17. [AI AGENT MODEL 전체 목록·역할·선정 기준·실행 상태](docs/17-ai-agent-model-registry.md)
18. [Claude Code 상세 인수인계·재현·다음 작업](docs/18-claude-code-handoff.md)
19. [AI Agent 1·2단계: 구축 가능성 진단·근거 등급 카탈로그·테마 매핑](docs/19-agent-step2-data-foundation.md)
20. [Langflow로 Agent 흐름 만들기: 설치·jeju-mcp 연결·보안](docs/20-langflow-agent-builder.md)
21. [Linux Docker 운영 배치·인증·영속성·배포 검증](docs/21-linux-docker-production.md)
22. [탈옥·프롬프트 주입 방어·벡터 검색 경계·합성 평가](docs/22-jailbreak-and-prompt-injection-defense.md)
23. [AI Agent 3단계: SynthTRIPs 방식 평가셋 생성기·정답 규칙·채점 지표](docs/23-agent-step3-evaluation-set.md)
24. [AI Agent 기준선 채점: 오프라인 하네스·Gemini/Langflow 응답 가져오기·자체 검사](docs/24-agent-baseline-scoring.md)
25. [실제 Gemini·Langflow 기준선 수집·중단 재개·잠정 판정](docs/25-live-baseline-execution.md)
26. [Langflow Vertex AI 인증 연결: 서비스 계정·Credential·흐름 가져오기](docs/26-langflow-vertex-auth.md)
27. [Agent 카탈로그 v2·원본 요청 기반 최종 검증·실행 제한](docs/27-agent-catalog-v2-validation.md)

Ollama의 공식 소스에서 모델 대화·구조화 출력·도구 호출·임베딩 기능을 확인했습니다. 논문 LAG 재현은 원문 확인과 별도 검증이 필요합니다. 기존 Vercel 앱의 화면·API를 유지하고 외부 Worker·사설 Ollama·HTTP MCP 서버를 연결하는 계획이며, 운영 앱에 배포한 상태는 아닙니다. 기존 SDK 1.32.1과 최신 MCP v2의 전송 규칙도 분리했습니다.

확장안은 공통 모델 라우터에서 OpenAI·Claude·Ollama를 선택하거나 제한적으로 병렬 비교합니다. 터널은 자체 서버 연결용이며 공급자별 API 호출에 각각 필요하지 않습니다. DB는 확인된 기존 PostgreSQL을 우선 재사용하고, 신규 선택 시 Supabase를 권장합니다. API 사용료는 ChatGPT·Claude 채팅 구독과 별도로 계획합니다.

기존 서비스 저장소의 CSV **55개**를 실제 검사해 [확정 데이터 카탈로그](data/confirmed-data-catalog.csv)와 [컬럼 사전](data/raw-data-columns.csv)을 만들었습니다. 원본·제주 추출·파생 집계를 구분하며, 논리행 합계 210,532행은 고유 관광지 수가 아닙니다. 원본 CSV ZIP은 로컬에서 재생성할 수 있으며, 일부 자료의 재배포 조건 확인이 남아 있어 공개 Git에는 포함하지 않습니다. [재현·권리 확인 절차](docs/14-confirmed-data-catalog.md)를 따릅니다. 원 Excel은 아직 없으므로 그 전체 항목을 대조한 것으로 표시하지 않습니다. 수신하면 [추적 양식](data/catalog-template.csv)에 시트·행을 보존해 매핑합니다.

## 보고서

- [페르소나 탐색기 — 응답을 바꾸며 유형 비교](reports/persona-explorer.html): 파일을 내려받아 브라우저에서 열 수 있는 독립 HTML. 외부 통신·사용자 입력 저장 없음.
- [8개 설계 후보의 특성 히트맵](reports/assets/persona-prototypes-heatmap.png)
- [설계 후보의 2차원 지도](reports/assets/persona-prototypes-map.png)
- [Ollama·MCP·Vercel 배포 설계도](reports/assets/ollama-mcp-vercel-architecture.png) · [비동기 처리 절차](reports/assets/ollama-mcp-job-sequence.png)
- [다중 모델·터널·DB 배치도](reports/assets/multi-provider-tunnels-database.png)
- [네임서버 저장 후 단계도](reports/assets/oreumit-next-steps.png) · [oreumit.com 다음 연결 배치도](reports/assets/oreumit-next-deployment.png)
- [Word 보고서](reports/jeju-personal-tour-agent-design.docx)
- [PDF 열람본](reports/jeju-personal-tour-agent-design.pdf)
- [Windows·macOS Ollama 연결 시험 안내](examples/ollama-tunnel/README.md) · [인증 Gateway 예제 ZIP](reports/ollama-tunnel-starter.zip): 모의 서버 테스트를 갖춘 연결 시험용 예제. 운영 Agent·MCP 서버 구현과 구분.
- [읽기 전용 MCP 서버 실행 안내](services/jeju-mcp/README.md): 서비스·HF 클라이언트·Agent 카탈로그·평가셋·기준선·최종 검증 179개 테스트와 로컬 SDK 연결 확인. 기존 v1 7,561곳과 별도 v2 981곳을 검증했습니다. 공개 배포는 미완료.
- [Agent 카탈로그 커버리지](reports/agent-catalog-coverage.json): 앱 장소 981곳의 속성·근거 등급·테마별 개수만 담은 집계. 카탈로그 본체는 `.local` 전용입니다.
- [평가셋 집계](reports/evaluation-set-summary.json): 96문항의 난이도·기대 동작·필수 조건 판정 개수와 정답 해시만 담은 집계. 질의와 정답 후보는 `.local` 전용입니다.
- [기준선 하네스 자체 검사](reports/baseline-harness-self-check.json): 복원한 템플릿 96문항을 정답 기반 응답으로 채점한 집계. 실제 모델 비교·지연·비용·보안 검증 결과가 아닙니다.
- [Gemini 실제 기준선 집계](reports/baseline-gemini-live-20261119.json): 96문항 시도, 지정 모델 응답 92건과 실패 4건. 자유 문장은 잠정 모델 판정이며 사람 검수·비용 측정은 미완료입니다.
- [Langflow 실제 기준선 집계](reports/baseline-langflow-live-20261119.json): 96문항 시도, 구조화 응답 80건과 실패 16건. 구조화 JSON을 수정 없이 채점했으며 비용은 미측정입니다. 두 실행 모두 미실행 0건이며 카탈로그 차이를 포함한 한계는 [25 문서](docs/25-live-baseline-execution.md)를 따릅니다.
- [확정 목록 CSV](data/confirmed-data-catalog.csv) · [실측 인벤토리 CSV](data/raw-data-inventory.csv) · [컬럼 사전 CSV](data/raw-data-columns.csv): 원본 CSV 파일은 공개 PR에 포함하지 않습니다.
- [Codex Hugging Face 설정](.codex/config.toml) · [Claude Code 설정](.mcp.json): 공개 모델·데이터셋·논문 조회용. 현재 클라우드 도메인 허용 초안의 적용 및 실제 연결 검증은 남아 있습니다.
- 시각화 재생성: `python3 scripts/build_persona_assets.py` (`numpy`, `matplotlib`, `Noto Sans CJK` 폰트 필요)
- 배포 설계도 재생성: `python3 scripts/build_deployment_diagrams.py` (`Graphviz dot` 필요)
- 연결 시험 ZIP 재생성: `python3 scripts/build_tunnel_bundle.py` (비밀 설정 파일 제외)
- 보고서 재생성: `python3 scripts/build_report.py --pdf` (`python-docx`, `pandoc`, `soffice`, `Noto Sans CJK` 폰트 필요)
- 데이터 목록 재생성: `python3 scripts/build_raw_data_inventory.py --source-repo <jeju-tour 체크아웃>` 다음 `python3 scripts/build_data_source_catalog.py --source <같은 경로>`. 로컬 ZIP도 필요하면 두 번째 명령에 `--bundle`을 추가합니다. 표준 Python만 사용하며 스크립트에서 네트워크를 호출하지 않습니다.

8개 유형은 [수작업 프로토타입 설정](data/persona-prototypes.json)이며 실제 응답으로 발견한 군집이 아닙니다. 히트맵과 지도는 이 설계값만 나타냅니다. 실제 군집 수·안정성·추천 효과는 동의받은 응답 데이터로 검증해야 합니다. 비교 점수는 소속 확률이나 만족도 확률이 아니며, 응답 부족·혼합형·유사 유형 없음 상태를 구분합니다.

## 작업 환경

기존 체크아웃 `/workspace/jeju-tour-data-analysis`를 사용합니다. 클라우드 작업은 이미 격리되어 있으므로 별도 Git worktree를 만들 필요가 없습니다. Python 3.12와 Git 읽기 접근을 확인했습니다. 이 환경에서는 `pip3` 실행 파일의 경로 문제가 있어 패키지 작업 시 `python3 -m pip`를 사용합니다.

MCP는 `services/jeju-mcp`에서 `npm ci --ignore-scripts --no-audit --no-fund`, `npm test`로 설치·검증합니다. 실행·비밀 설정·실제 자료 가져오기는 해당 README를 따릅니다. 별도 Ollama 연결 예제는 `node --test examples/ollama-tunnel/gateway.test.mjs`로 모의 업스트림 계약을 검증할 수 있습니다. 모델 다운로드, GPU 배포, 데이터 API 연결은 논문·데이터와 운영 조건을 확인한 후 진행합니다. MCP 통신·문서 검증은 추천 품질 검증을 대신하지 않습니다.

자동 검사는 [CI 워크플로](.github/workflows/ci.yml)가 담당합니다. 기반 MCP·CI 커밋 `ad3c363`의 [main 실행 성공](https://github.com/OREUM-IT/jeju-tour-data-analysis/actions/runs/44226883163)을 확인했습니다. 문서·메타데이터 검사는 `python3 scripts/check_repository.py`로 실행합니다. 공개 HF MCP 실연결, 조직 생성, Ollama 추론, Vercel 운영 통합은 별도 검증 항목입니다.
