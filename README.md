# 제주도 맞춤 개인 관광 페르소나(테마) 추천 AI Agent

관광 설문을 여행 선호도와 실행 제약으로 변환하고, 그 근거를 설명하는 개인화 추천 Agent의 기획 저장소입니다. 사용자가 지정한 논문 **2504.09277의 LAG 방법**을 주 연구 방향으로 검토합니다. LAG를 RAG의 오타나 동의어로 해석하지 않습니다.

## 현재 상태

- **다른 Claude Code 세션의 시작점:** [CLAUDE.md](CLAUDE.md) → [상세 인수인계·재개 절차](docs/18-claude-code-handoff.md) → [전체 AI AGENT MODEL 목록](docs/17-ai-agent-model-registry.md). 모델별 구현·실행·평가 상태는 [JSON 레지스트리](data/ai-agent-models.json)에서도 관리합니다.
- 상세 설계와 읽기 전용 MCP 초기 구현 단계입니다. 장소 검색·상세·조건 검사 3개 도구를 로컬 검증했습니다. 전체 추천 Agent·학습 모델·운영 통합은 후속 작업입니다.
- 기존 서비스: <https://jeju-tour-six.vercel.app/>. 관련 OREUM-IT 저장소의 소스 검토 결과는 아래 문서에 기록합니다.
- 논문 원문, 별도 PDF 및 제주데이터허브 목록 엑셀은 아직 내용 확인을 완료하지 못했습니다. 실제 데이터셋과 논문의 방법·성능을 추정해서 기재하지 않습니다.
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

Ollama의 공식 소스에서 모델 대화·구조화 출력·도구 호출·임베딩 기능을 확인했습니다. 논문 LAG 재현은 원문 확인과 별도 검증이 필요합니다. 기존 Vercel 앱의 화면·API를 유지하고 외부 Worker·사설 Ollama·HTTP MCP 서버를 연결하는 계획이며, 운영 앱에 배포한 상태는 아닙니다. 기존 SDK 1.32.1과 최신 MCP v2의 전송 규칙도 분리했습니다.

확장안은 공통 모델 라우터에서 OpenAI·Claude·Ollama를 선택하거나 제한적으로 병렬 비교합니다. 터널은 자체 서버 연결용이며 공급자별 API 호출에 각각 필요하지 않습니다. DB는 확인된 기존 PostgreSQL을 우선 재사용하고, 신규 선택 시 Supabase를 권장합니다. API 사용료는 ChatGPT·Claude 채팅 구독과 별도로 계획합니다.

기존 서비스 저장소의 CSV **55개**를 실제 검사해 [확정 데이터 카탈로그](data/confirmed-data-catalog.csv)와 [컬럼 사전](data/raw-data-columns.csv)을 만들었습니다. 원본·제주 추출·파생 집계를 구분하며, 논리행 합계 210,532행은 고유 관광지 수가 아닙니다. 원본 CSV ZIP은 로컬에서 재생성할 수 있으며, 일부 자료의 재배포 조건 확인이 남아 있어 공개 Git에는 포함하지 않습니다. [재현·권리 확인 절차](docs/14-confirmed-data-catalog.md)를 따릅니다. 원 Excel은 아직 없으므로 그 전체 항목을 대조한 것으로 표시하지 않습니다. 수신하면 [추적 양식](data/catalog-template.csv)에 시트·행을 보존해 매핑합니다.

## 보고서

- [설문지 Word](reports/jeju-tour-questionnaire.docx) · [PDF](reports/jeju-tour-questionnaire.pdf): 핵심 10문항·조건부 10문항·선택적 선호 3문항, 응답란을 포함한 8페이지 문항 검토용 초안.
- [설문→프로필 예제 노트북](reports/jeju-survey-to-profile.ipynb) · [설문 파일 다운로드 ZIP](reports/jeju-survey-downloads.zip): 표준 Python으로 실행하는 제한된 변환 예제와 6개 핵심 검사. 전체 설문·추천 엔진 구현과 구분합니다. ZIP은 설문 Word·PDF·노트북 3개만 포함하며 원본 데이터는 포함하지 않습니다.
- [페르소나 탐색기 — 응답을 바꾸며 유형 비교](reports/persona-explorer.html): 파일을 내려받아 브라우저에서 열 수 있는 독립 HTML. 외부 통신·사용자 입력 저장 없음.
- [8개 설계 후보의 특성 히트맵](reports/assets/persona-prototypes-heatmap.png)
- [설계 후보의 2차원 지도](reports/assets/persona-prototypes-map.png)
- [Ollama·MCP·Vercel 배포 설계도](reports/assets/ollama-mcp-vercel-architecture.png) · [비동기 처리 절차](reports/assets/ollama-mcp-job-sequence.png)
- [다중 모델·터널·DB 배치도](reports/assets/multi-provider-tunnels-database.png)
- [네임서버 저장 후 단계도](reports/assets/oreumit-next-steps.png) · [oreumit.com 다음 연결 배치도](reports/assets/oreumit-next-deployment.png)
- [Word 보고서](reports/jeju-personal-tour-agent-design.docx)
- [PDF 열람본](reports/jeju-personal-tour-agent-design.pdf)
- [Windows·macOS Ollama 연결 시험 안내](examples/ollama-tunnel/README.md) · [인증 Gateway 예제 ZIP](reports/ollama-tunnel-starter.zip): 모의 서버 테스트를 갖춘 연결 시험용 예제. 운영 Agent·MCP 서버 구현과 구분.
- [읽기 전용 MCP 서버 실행 안내](services/jeju-mcp/README.md): 서비스·HF 클라이언트 32개 테스트와 로컬 SDK 연결 확인. 기존 자료 7,561건 가져오기 검증. 공개 배포는 미완료.
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
