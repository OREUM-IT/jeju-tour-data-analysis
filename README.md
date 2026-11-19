# 제주도 맞춤 개인 관광 페르소나(테마) 추천 AI Agent

2026-11-19 최신 작업: 사용자 요청으로 [PR #19](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/19)의 후보 표시 복구에 **동시 실행 기본 2건·대기 최대 8건의 요청 큐**를 추가한다. 시간당 기본 한도는 전체 60건·사용자 12건이며 범위 안에서 설정한다. 완료가 확인된 오류와 실행 여부가 불확실한 오류를 구분하고, 후자는 기존 보호 시간을 유지한다. 상태 조회는 재실행하지 않으며 재시작 뒤 대기 작업을 복원·재전송하지 않는다. [AWS 적용 안내](docs/32-agent-gateway-tunnel.md#요청-큐와-동시-처리-업데이트)의 새 helper는 MCP·Gateway 두 이미지를 먼저 빌드하고 실행·대기 상태가 비었을 때 두 서비스만 갱신한다. **AWS 8GB 환경의 실제 모델 2건 동시 처리와 이번 변경의 실사용 결과는 미검증**이며 `productionReady:false`다.

2026-11-19 이전 후보 표시 완화: 사용자가 운영 설문에서 “조건과 장소를 확인할 수 없어 AI 결과를 표시하지 않았어요”를 보고하고 후보를 먼저 보여 달라고 요청했다. 이 문구는 Gateway HTTP 422와 웹 음식점 제외 처리에서 공통으로 발생하므로 개별 요청의 원인은 아직 확정하지 않았다. `codex/gateway-candidate-recovery`는 Gateway에 등록된 원본 요청 범위 안에서 모델이 선택한 장소 ID만 남기고, 조건 판정·확인 안내를 서버 근거로 재구성한 뒤 기존 엄격한 최종 검증을 다시 통과시킨다. 일반 `/validate-response`와 기준선 평가는 바꾸지 않는다. 이 후보 복구만의 초기 변경은 MCP 서비스 갱신으로 호환되지만, 이후 요청 큐까지 적용할 때는 위 안내의 MCP·Gateway 갱신이 필요하다. 구현·로컬 검증과 사용자 AWS 적용은 구분하며 아직 이번 수정의 AWS 적용·실제 웹 추천 성공은 확인하지 않았다.

2026-11-19 이전 Preview 연결 기록: 사용자 요청으로 실제 Vercel 앱 저장소의 [PR #33](https://github.com/OREUM-IT/jeju-tour/pull/33)에 **로그인한 설문 사용자 → Vercel 서버 → Gateway → Langflow/Vertex AI** 연결을 구현했다. 로컬 테마 계산과 AI 장소 결과를 구분하고 미검증 조건을 유지한다. 서버·설문 24개, 비밀 등록 15개, 신규/기존 브라우저 18개 검사가 통과했다. 사용자가 AWS 비밀 3개를 해당 Preview 브랜치에 등록했고 Vercel 메타데이터로 범위를 확인했다. 비밀 등록 후 `7ec1360` Preview 배포가 READY이며 `/api/agent/status`의 HTTP 200·`enabled:true`·`configured:true`를 확인했다. 실제 로그인 사용자의 추천 응답 확인은 남아 있다. 모델 호출 없는 모의 검사와 웹 실응답 검증을 구분하며 `productionReady:false`를 유지한다. [웹 연결 절차](https://github.com/OREUM-IT/jeju-tour/blob/codex/vercel-agent-gateway/docs/agent-gateway-vercel.md)를 따른다.

2026-11-19 최신: 사용자 AWS에서 **Cloudflare HTTPS·Access 서비스 인증·Gateway 앱 HMAC/JWT를 거치는 공개 준비 검사(`scope:public`)가 통과**했다. [PR #17](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/17)의 `7890060`을 적용해 검사기 User-Agent의 Cloudflare `1010`과 동일 JWT의 `CF_Authorization` 쿠키 호환 문제를 해결했다. [검증 결과와 범위](docs/32-agent-gateway-tunnel.md#aws-공개-인증-검사-성공)는 사용자 제공 출력에 근거한다. 다음은 **Vercel 설문 서버 어댑터·서버 비밀 설정·Preview 연결 검사**다. 이번 검사는 모델을 실행하지 않았으며 실제 웹 사용자 추천과 운영 전체 검증 전이므로 `productionReady:false`를 유지한다.

2026-11-19 AWS 최신 진행: 사용자가 **`143b705` 적용 후 Vertex 실제 요청 1건 성공** 출력을 제공했다. Langflow는 41초에 Healthy가 됐고, 흐름은 `upgraded-vertex-client`, `vertexConfigured:true`다. 실제 요청은 HTTP 200·14.5초·최종 검증 수락으로 완료됐으며 모델 호출 기록 4회 모두 성공, MCP 도구 호출 3회 성공, 예상 밖 도구 호출 0회다. trace 1건은 정상 종료했고 임시 실행 키도 폐기됐다. 후보 3곳에 대한 `cannot_confirm`·`indoor_unverified`는 **미검증 실내 조건을 확정하지 않고 확인 필요를 정상 안내한 결과**다. [상세 실행 기록](docs/31-lightsail-langflow.md#4-실제-모델-요청-1건-검사)은 사용자 제공 출력에 근거하며 작성자의 AWS 원격 실행 결과가 아니다. 단건 연결·검증 성공이며 추천 품질 전체나 운영 준비 완료를 뜻하지 않아 `productionReady:false`를 유지한다. [PR #14](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/14)는 CI 통과 후 `main@4579c25`에 병합됐다. 완료된 단건 요청을 다시 확인하기 위한 반복 실행은 필요하지 않다. 이 단건 성공 이후 Gateway 구현·Tunnel 기동까지 진행했으며, 최신 공개 인증 상태와 다음 작업은 위 최신 문단을 따른다.

관광 설문을 여행 선호도와 실행 제약으로 변환하고, 그 근거를 설명하는 개인화 추천 Agent의 기획 저장소입니다. 처음 지정된 논문 **2504.09277은 SynthTRIPs**(관광 추천기 평가용 합성 질의 생성 틀)로 확인되어 평가셋 설계에 쓰고, **LAG는 2508.05509 Logic-Augmented Generation**을 Agent 추론 비교 실험으로 검토합니다(2026-11-18 사용자 결정, [19 문서](docs/19-agent-step2-data-foundation.md)). LAG를 RAG의 오타나 동의어로 해석하지 않습니다.

## 현재 상태

- **요청 큐·동시 처리 적용 준비:** 기존 후보 복구 PR #19에 큐를 추가하며 웹도 한 번 제출한 작업의 상태만 조회하도록 연결합니다. 사용자당 실행·대기 합계 1건, 기본 전체 실행 2건·대기 8건을 제한합니다. 적용 helper는 활성 MCP scope와 Gateway 실행·대기·불확실 lease를 읽기 전용으로 확인합니다. AWS 적용과 실제 병렬 모델 실행은 아직 확인하지 않았습니다.
- **기존 Vercel Production 연결·설문 저장 배포 완료:** 서버 어댑터·결과 화면·Production 설정에 이어 설문 저장·불러오기를 배포했습니다. 상태 API의 활성화/설정 완료와 브라우저 저장→새로고침→답변 복원을 확인했습니다. 이후 사용자가 AI 결과 차단 문구를 보고하여 위 후보 복구·큐 작업으로 이어졌습니다. [운영 확인 기록](docs/18-claude-code-handoff.md#0-지금-이어받을-상태-2026-11-19)과 이번 변경의 미검증 범위를 구분합니다.
- **Gateway·Tunnel 공개 인증 검사 통과:** 사용자 AWS에서 무인증 거부 → Access 인증만 있으면 앱 인증 거부 → 두 인증을 갖추면 준비 응답 성공을 확인했습니다. 검사기 User-Agent와 Cloudflare 인증 쿠키 호환 문제를 해결했고 기존 인증 검증은 유지합니다. 이후 Vercel 연결까지 진행했으며 모델 실행·실제 웹 사용자 추천·운영 준비 검증은 별도입니다.
- **AWS Vertex 단건 실응답 성공(사용자 제공 출력):** [실제 981곳 MCP·Langflow·Vertex 연결](docs/31-lightsail-langflow.md)에서 HTTP 200, 모델 성공 4회·MCP 호출 성공 3회, 최종 검증 수락과 임시 키 폐기를 확인했습니다. 실내 조건을 확정할 근거가 없어 후보 3곳을 `cannot_confirm`으로 안내한 정상 결과입니다. 단건 성공 범위이며 `productionReady:false`입니다. 이후 Gateway·Tunnel 공개 인증 검사와 Vercel 연결까지 진행했습니다.
- **Lightsail 초기 구성:** [비공개 Docker 기동 안내](docs/29-lightsail-docker-bootstrap.md)와 [Compose](deploy/compose.yaml)를 추가했습니다. 가상 데이터 MCP와 PostgreSQL·pgvector를 독립 실행하는 초기 구성입니다. 검증 환경에서 MCP SDK·인증·DB 연산을 확인했으며 실제 AWS 적용, 관광 데이터/DB 연동, Langflow·Tunnel·Vercel 연결 완료를 뜻하지 않습니다.
- **v2 96문항 실측·채점 완료:** [PR #9](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/9) 병합(`main@4b41a03`) 후 [PR #10](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/10)에서 같은 981곳·새 최종 검증 흐름을 평가했습니다. [채점 집계](reports/baseline-langflow-v2-live-20261119.json)는 정상 응답 73·실패 23·미실행 0입니다. 허용 밖 ID는 162/305에서 7/295로 줄었지만 전체 동작 정답은 67/96으로 같고 확인 고지는 35/36에서 17/31로 낮아졌습니다. [28 문서](docs/28-agent-v2-evaluation.md)에 관심 테마·걷기·날짜 충돌의 계약 차이와 비교 한계를 기록했습니다. 전체 품질 개선이나 모델 우열을 주장하지 않습니다.
- **카탈로그 v2·최종 검증 구현:** [PR #8](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/8) 병합 후 `codex/agent-catalog-v2-validation`에서 앱과 같은 981곳의 MCP, 원본 요청에 따른 최종 ID·필수 조건 검증, 검증 전 출력을 억제하는 [별도 Langflow 흐름](services/langflow/jeju-tour-agent-vertex-v2.flow.json)을 추가했습니다. 로컬 SDK·모의 모델 검사와 실제 Gemini 실행 요청 1건에서 미확인 실내 조건을 `cannot_confirm`으로 안내하는 동작을 확인했습니다. [27 문서](docs/27-agent-catalog-v2-validation.md)에 검증 범위와 한계를 기록합니다. 이후 별도 96문항 평가를 완료했으며 운영 배포는 하지 않았습니다.
- **실제 Gemini·Langflow 수집·채점 완료(2026-11-19):** 같은 계획의 96문항씩을 모두 시도했습니다. [Gemini 집계](reports/baseline-gemini-live-20261119.json)는 지정 모델 응답 92건·실패 4건(혼합 대체 모델 3건·시간 초과 1건), [Langflow 집계](reports/baseline-langflow-live-20261119.json)는 구조화 응답 80건·실패 16건이며 두 실행 모두 미실행 0건입니다. Google Developer API의 503을 진단한 뒤 [Vertex AI 인증](docs/26-langflow-vertex-auth.md)으로 같은 `gemini-3.8-flash`의 Langflow 응답·MCP 도구 호출을 수집했습니다. 결과와 한계는 [25 문서](docs/25-live-baseline-execution.md), 변경은 [PR #8](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/8)에 있습니다. Gemini 자유 문장 판정은 잠정 모델 검수이며 사람 검수는 0/24건, 두 실행의 비용은 미측정입니다. 카탈로그·날짜 문맥이 달라 모델 자체의 우열을 판단하지 않습니다. [채점기 자체 검사](reports/baseline-harness-self-check.json)는 별도의 oracle 검사입니다.
- **운영·보안 설계 추가:** [Linux Docker 전환안](docs/21-linux-docker-production.md)과 [탈옥·프롬프트 주입 방어](docs/22-jailbreak-and-prompt-injection-defense.md)를 정리했습니다. 최종 구조화 응답의 ID·필수 조건 검증은 [27 문서](docs/27-agent-catalog-v2-validation.md) 범위로 구현했지만 전체 도구 권한·비용 통제와 [합성 평가 14개](data/agent-security-cases.json)는 미완료입니다. 현재 자료는 JSON 카탈로그, PostgreSQL + pgvector는 향후 검색 저장소 후보입니다.
- **AI Agent 단계 진행:** [1·2단계 진단과 데이터 기반](docs/19-agent-step2-data-foundation.md)을 마쳤습니다. 원본 앱과 같은 장소 981곳을 속성별 근거 등급과 함께 내보내는 변환기, 페르소나 테마↔앱 분류 [매핑](data/theme-category-map.json), 이름·ID 없는 [커버리지 집계](reports/agent-catalog-coverage.json)를 추가했습니다. `verified` 0건·비용 0건이라 필수 조건은 아직 판정할 수 없습니다. [3단계 평가셋](docs/23-agent-step3-evaluation-set.md)도 만들었습니다. 8개 페르소나 × 12개 슬롯 96문항의 정답을 규칙으로 계산하는 생성기·채점 함수·테스트와 이름·ID 없는 [집계 보고서](reports/evaluation-set-summary.json)가 있고, 질의와 정답 후보는 `.local`에만 둡니다. 카탈로그 v2 연결·최종 구조화 응답 검증·Agent 90초 및 모델 4회 제한은 구현했습니다. 다음은 검색·조건 검사·최종 응답의 호출 흐름 제한, 문맥 조건과 런타임 계약 정합성 보강, 사람 24문항 검수, 원격 취소·비용 계측과 합성 보안 사례 14개 평가입니다. 작업 명세와 재개 프롬프트는 [인수인계 11·12절](docs/18-claude-code-handoff.md)에 있습니다.
- **시각적 Agent 빌더:** 사용자 결정으로 [Langflow](docs/20-langflow-agent-builder.md)를 씁니다(Flowise는 2026-08-31 지원 종료). Langflow 1.13.3에서 jeju-mcp 도구 3개와 실제 모델 응답을 확인했습니다. [기본 시작 흐름](services/langflow/jeju-tour-agent.flow.json)과 키를 포함하지 않는 [Vertex AI 흐름](services/langflow/jeju-tour-agent-vertex.flow.json)을 제공합니다. 기존 실측의 MCP v1 7,561곳과 앱·평가셋 981곳은 서로 다릅니다. [새 v2 흐름](services/langflow/jeju-tour-agent-vertex-v2.flow.json)은 같은 981곳과 최종 검증을 연결했으며, 별도 96문항 평가에서 정상 응답 73건과 실패 23건을 기록했습니다. 같은 카탈로그여도 모든 문맥 조건을 강제한 것은 아닙니다.
- **다른 Claude Code 세션의 시작점:** [CLAUDE.md](CLAUDE.md) → [상세 인수인계·재개 절차](docs/18-claude-code-handoff.md) → [전체 AI AGENT MODEL 목록](docs/17-ai-agent-model-registry.md). 모델별 구현·실행·평가 상태는 [JSON 레지스트리](data/ai-agent-models.json)에서도 관리합니다.
- 상세 설계와 읽기 전용 MCP 초기 구현 단계입니다. 장소 검색·상세·조건 검사 3개 도구를 로컬 검증했습니다. 전체 추천 Agent·학습 모델·운영 통합은 후속 작업입니다.
- 기존 서비스: <https://jeju-tour-six.vercel.app/>. 관련 OREUM-IT 저장소의 소스 검토 결과는 아래 문서에 기록합니다.
- 두 논문의 본문 세부, 별도 PDF 및 제주데이터허브 목록 엑셀은 아직 내용 확인을 완료하지 못했습니다. 실제 데이터셋과 논문의 방법·성능을 추정해서 기재하지 않습니다.
- 문항·점수·일정·평가 목표는 검증 전 제안입니다. 기존 서비스에서 확인한 사실과 새 제안을 구분합니다.

## 문서 읽는 순서

현재 AWS 후속 실행은 [32. 제한 Gateway·Cloudflare Access/Tunnel 연결](docs/32-agent-gateway-tunnel.md)을 따른다.

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
28. [카탈로그 v2 Agent 96문항 평가: 템플릿 요청 변환·별도 수집·비교 한계](docs/28-agent-v2-evaluation.md)
29. [Lightsail Docker 설치 이후: 비공개 MCP·PostgreSQL·pgvector 기동](docs/29-lightsail-docker-bootstrap.md)
30. [AWS MCP에 실제 981곳 카탈로그 연결](docs/30-lightsail-real-catalog.md)
31. [AWS Langflow 내부 연결과 기존 Vertex 자격증명 등록](docs/31-lightsail-langflow.md)

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
- [읽기 전용 MCP 서버 실행 안내](services/jeju-mcp/README.md): 서비스·HF 클라이언트·Agent 카탈로그·평가셋·기준선·최종 검증·v2 요청 수집 198개 테스트와 로컬 SDK 연결 확인. 기존 v1 7,561곳과 별도 v2 981곳을 검증했습니다. 공개 배포는 미완료.
- [Agent 카탈로그 커버리지](reports/agent-catalog-coverage.json): 앱 장소 981곳의 속성·근거 등급·테마별 개수만 담은 집계. 카탈로그 본체는 `.local` 전용입니다.
- [평가셋 집계](reports/evaluation-set-summary.json): 96문항의 난이도·기대 동작·필수 조건 판정 개수와 정답 해시만 담은 집계. 질의와 정답 후보는 `.local` 전용입니다.
- [기준선 하네스 자체 검사](reports/baseline-harness-self-check.json): 복원한 템플릿 96문항을 정답 기반 응답으로 채점한 집계. 실제 모델 비교·지연·비용·보안 검증 결과가 아닙니다.
- [Gemini 실제 기준선 집계](reports/baseline-gemini-live-20261119.json): 96문항 시도, 지정 모델 응답 92건과 실패 4건. 자유 문장은 잠정 모델 판정이며 사람 검수·비용 측정은 미완료입니다.
- [Langflow 실제 기준선 집계](reports/baseline-langflow-live-20261119.json): 96문항 시도, 구조화 응답 80건과 실패 16건. 구조화 JSON을 수정 없이 채점했으며 비용은 미측정입니다. 두 실행 모두 미실행 0건이며 카탈로그 차이를 포함한 한계는 [25 문서](docs/25-live-baseline-execution.md)를 따릅니다.
- [Langflow v2 실측 집계](reports/baseline-langflow-v2-live-20261119.json) · [실행 감사](reports/agent-v2-runtime-audit-20261119.json): 96시도·73응답·23실패, 모델 span 255개·MCP 호출 180회. 저장된 span 상태와 최종 응답 성공을 구분하며 비용은 미측정입니다. 자동 검사 240개와 저장소 검사 14개를 통과했습니다.
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
