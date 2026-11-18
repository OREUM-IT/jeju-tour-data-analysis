# Claude Code 시작 안내

이 저장소는 제주 관광 Agent의 설계·자료 검토·읽기 전용 MCP 초기 구현이다. 먼저 [인수인계](docs/18-claude-code-handoff.md)를 읽고 현재 Git 상태와 PR head를 확인한다. 모델의 역할·근거·미검증 상태는 [모델 레지스트리](docs/17-ai-agent-model-registry.md)와 [JSON](data/ai-agent-models.json)을 따른다.

- 기존 작업을 보존한다. 분석 저장소의 전체 작업 PR과 그 본문을 이어서 관리하고, 이미 승인된 브라우저 조작·무료 HF 조직 생성·MCP 연결·CI·PR 작업에 다시 포괄 승인을 묻지 않는다. 실행 도구·인증이 없으면 필요한 접근만 구체적으로 설명한다.
- `OREUM-IT/jeju-tour`와 `jeju-tour-flutter`는 고정 커밋의 읽기 전용 참고 대상이다. 원본 앱·운영 배포·DB·DNS·참고 저장소에 임의 수정, push, 삭제를 하지 않는다. 분석 저장소의 PR 작업과 구분한다.
- LAG는 논문 `2504.09277`의 방법이다. 원문 미확인 상태에서 RAG나 GraphRAG로 재정의하지 않는다. 8개 페르소나는 수작업 설계이며 실제 관광객 군집·소속 확률이 아니다.
- MCP 모의 검사, 실제 HF 연결, 모델 추론, 운영 배포의 성공을 구분한다. 기존 성공한 42개 테스트를 문서 변경만으로 반복·확장하지 않는다. 코드 변경이나 새 환경 검증이 필요할 때만 관련 검사를 실행한다.
- 새 환경 설치는 Node 24.22.1에서 `npm --prefix services/jeju-mcp ci --ignore-scripts --no-audit --no-fund`를 사용한다. 잠금 SDK 1.32.1을 유지하고 MCP v2 규칙을 섞지 않는다.
- `.mcp.json`은 Claude Code 프로젝트 설정이다. 파일 존재만으로 현재 세션에서 MCP가 활성화되거나 인증됐다고 주장하지 않는다. 실제 연결 절차는 [HF 설정](docs/15-huggingface-mcp-setup.md)과 [조직·CI 안내](docs/16-huggingface-organization-and-ci.md)를 따른다.
- 비밀값·쿠키·보호된 전역 설정을 출력하거나 Git·보고서·ZIP에 넣지 않는다. 프록시, TLS, 프로젝트 신뢰 정책을 우회하지 않는다. 자료의 보유·공개 여부를 재배포·학습 이용 허가로 바꾸지 않는다.
- 원본 CSV ZIP은 라이선스 검토 전 로컬 재생성용이다. 공개 PR에는 빌더·카탈로그를 포함하고 `reports/jeju-data-snapshot-bundle.zip`은 제외한다.

사용자와 문서는 한국어로 소통하고, 구현·관측 사실·사용자 보고·제안을 구분해 기록한다.
