## 변경 목적

Linux Docker로 Langflow·제주 MCP를 운영할 때의 배치와 탈옥·프롬프트 주입 방어 경계를 정리합니다. 현재 MCP가 안전한 도구 결과를 반환해도 Agent가 최종 설명에서 잘못된 장소나 조건 판정을 만들 수 있으므로, 실제 도구 실행 시점의 정책 검사와 최종 결과 검증을 별도 구현 항목으로 명시했습니다.

## 변경 내용

- `docs/21-linux-docker-production.md`: 컨테이너 네트워크·바인딩, 비공개 편집기·DB, Access·Tunnel·앱 인증, 비밀·영속성·백업·롤백과 배포 검증 단계.
- `docs/22-jailbreak-and-prompt-injection-defense.md`: 사용자 입력·검색 자료·MCP 결과의 신뢰 경계, 도구·사용자 권한, 누적 예산, 최종 ID·필수 조건·출력 렌더링 검증.
- `data/agent-security-cases.json`: 방어 12개·정상 요청 2개의 합성 평가 명세. 실제 부작용·유출·제약 위반을 판정하며 전부 미실행으로 표시.
- 현재 JSON 카탈로그와 향후 PostgreSQL + pgvector 후보를 구분. 벡터 검색은 후보 탐색에 사용하고 사실·필수 조건은 정형 근거와 서버 코드로 검사.
- Langflow 문서의 도구 보호 범위 설명을 수정하고 README·CLAUDE·인수인계·참고 근거·Word/PDF 보고서에 반영.

## 논문 확인 범위

`2403.17336`은 저자 공식 `WUSTL-CSPL/LLMJailbreak`의 고정 README(`2a9e665769de1bbfa657b6e6a9f88d9d427bc57c`)로 제목·arXiv ID·USENIX Security 2024 연결과 공격 평가 연구임을 확인했습니다. alphaXiv·arXiv·USENIX 본문은 환경에서 403으로 차단되어 PDF·세부 실험 표는 검토하지 못했습니다. 본 PR의 운영 통제가 논문에서 효과를 검증한 방어 알고리즘이라고 주장하지 않습니다.

## 검증과 제한

- `python3 scripts/check_repository.py`: 기존 오프라인 문서·메타데이터·산출물 검사.
- 합성 사례 JSON 파싱, 14개 고유 ID, `planned_not_executed`·`noLiveCalls` 상태 확인.
- Word/PDF 재생성, 압축 구조·표 내용·추가 장 렌더링 확인.
- 애플리케이션 코드·모델 호출·Docker 이미지·운영 서버·DNS·DB·비밀 설정은 변경하지 않았습니다. 정책 게이트·최종 검증기·벡터 DB는 설계 상태이며 실제 탈옥 방어 평가는 미실행입니다.

기준은 `main@725737f`이며 기존 설문 v0.3 등 다른 브랜치의 후속 변경을 자동 병합하지 않습니다. 검토 후 병합하는 PR입니다.
