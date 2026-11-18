# Hugging Face 공식 MCP 호출 설정

기준일: 2026-11-18 한국 시간. 공식 서버의 모델·데이터셋·논문 검색을 개발 작업에 연결한다. **프로젝트 설정과 호출 도구를 준비했으며, 현재 환경의 Hugging Face 접속이 프록시에서 차단되어 운영 서버 연결 성공은 아직 확인하지 못했다.**

## 1. 저장한 설정

| 대상 | 파일·값 | 적용 범위 |
|---|---|---|
| Codex 프로젝트 | [`.codex/config.toml`](../.codex/config.toml) | `https://huggingface.co/mcp?gradio=none`, 허용 도구 3개, 시작·호출 제한 시간 30초 |
| Claude Code 프로젝트 | [`.mcp.json`](../.mcp.json) | `https://huggingface.co/mcp?bouquet=files&gradio=none`, 읽기 파일 도구 preset |
| 독립 MCP 호출 | [호출 스크립트](../services/jeju-mcp/scripts/huggingface-client.mjs) | 모델·데이터셋·논문 검색, 지정 논문 읽기. 기존 잠금 SDK 사용 |
| 클라우드 네트워크 | 환경 설정 초안에 `huggingface.co` 추가 | 기존 도메인을 보존. 초안 저장과 현재 런타임 적용은 별도 |

설정에는 토큰 값이 없다. 공식 소스가 지원하는 익명 공개 조회로 시작한다. 현재 대화의 도구 목록에 Hugging Face가 실시간 추가되었다는 뜻은 아니다. Codex 프로젝트 설정은 클라이언트의 신뢰 정책과 다시 연결한 세션에서의 로딩에 따르며, Claude Code도 프로젝트 MCP 설정 확인 절차가 있다. Claude 웹·Desktop의 커넥터 등록은 이 파일과 별도다.

Codex 설정은 공식 CLI에 명시적인 설정 오버라이드로 전달해 URL·허용 도구·시간 제한의 파싱을 확인했다. 이 환경에서 단순 `codex mcp get huggingface`로 자동 로딩되지는 않았으므로, 프로젝트 자동 활성화까지 검증한 것으로 기록하지 않는다. 보호된 전역 설정이나 프로젝트 신뢰 설정은 변경하지 않았다.

## 2. 공식 주소와 사용할 도구

기본 주소는 <https://huggingface.co/mcp>이며 전송은 Stateless Streamable HTTP JSON이다. 공식 소스 커밋 `16ebc9579c25266032ac2234414b9dd98fd96cf4`, 패키지 `0.4.34`를 확인했다. 이는 현재 운영 서버가 같은 커밋으로 배포됐다는 확인은 아니다.

| 도구 | 역할 | 이 설정에서의 사용 |
|---|---|---|
| `hub_repo_search` | 모델·데이터셋 등의 통합 검색 | Codex 허용 목록. CLI는 model/dataset 검색만 구성 |
| `hub_repo_details` | 저장소 설명·데이터셋 구조·표본 조회 | Codex 허용 목록. 수동 연구에서 자료 조건 확인 |
| `hf_fs` | Hub 자료·논문·문서 검색과 읽기 | 논문 검색·본문 조회, Claude Code 최소 preset |

`bouquet=files`는 `hf_fs`를 선택하고 고정 도구 `hf_whoami`가 추가될 수 있다. `hub_repo_search`까지 포함하는 preset은 아니다. `hf_fs` 자체로도 모델·데이터셋·논문 검색을 할 수 있다.

공식 최신 소스에서 예전 `model_search`, `dataset_search`, `paper_search` 이름은 폐기된 도구로 기록되어 있다. 실제 연결에서는 `tools/list`의 결과와 입력 스키마를 먼저 확인한다. URL의 bouquet·gradio 필터는 도구 노출 선택이며 서버 권한 경계는 아니다. Codex는 `enabled_tools`를 사용하고 독립 호출 스크립트는 로컬 허용 목록·인자 구성을 적용한다.

## 3. 실제 호출 명령

현재 클라우드의 Node.js는 24.22.1이다. 아래 명령은 분석 저장소 루트 기준이며 기존 `services/jeju-mcp/node_modules`의 잠금 SDK를 사용한다. 의존성이 없는 새 환경에서는 서비스 폴더에서 README의 `npm ci` 절차를 먼저 수행한다.

```sh
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs list
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs models "Korean instruction"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs datasets "Jeju tourism"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs papers "tourism recommendation"
node --use-env-proxy services/jeju-mcp/scripts/huggingface-client.mjs paper 2504.09277
```

`--use-env-proxy`는 현재 Node에서 확인한 옵션이며, 제공된 HTTPS 프록시·인증서 설정을 사용한다. 프록시를 제거하거나 TLS 검증을 끄지 않는다. 공개 검색 성공이 빈 결과로 끝날 수도 있으므로, 도구 실행 성공과 관련 데이터 발견은 구분한다.

모델·데이터셋 검색의 현재 공식 인자 예:

```json
{"query":"Jeju tourism","repo_types":["dataset"],"limit":5}
```

논문 검색의 현재 공식 인자 예:

```json
{"operations":[{"cmd":"search","args":["hf://papers","tourism recommendation","--limit","5"]}]}
```

논문 `paper.md`는 원문 Markdown이 없으면 **메타데이터·초록·AI 요약 fallback**을 반환할 수 있다. 정상 응답을 받았어도 full-text availability·잘림 여부를 확인한다. 본문 제공 여부를 확인하기 전에는 논문 LAG의 정의·결과를 검토 완료로 표시하지 않는다.

## 4. 계정과 인증이 필요한 경우

공식 소스는 공개 조회의 익명 도구를 지원한다. 다만 실제 운영 서버가 strict-token 모드인지, 현재 계정·호출 한도에서 사용할 수 있는지는 라이브 연결로 확인해야 한다.

개인화·비공개 자료 접근이 필요하면 클라이언트에서 공식 OAuth 주소 `https://huggingface.co/mcp?login`으로 계정을 연결하거나, 독립 호출 스크립트에 보안 설정으로 `HF_TOKEN`을 제공한다. 스크립트는 토큰이 있을 때만 HTTPS Authorization Bearer 헤더에 전달하며 값을 출력하지 않는다. 현재는 HF_TOKEN을 필수 요구사항으로 등록하지 않았다.

Codex에서 환경변수 인증을 선택할 경우 기존 `[mcp_servers.huggingface]` 블록에 `bearer_token_env_var = "HF_TOKEN"`을 추가한다. 실제 환경에 해당 변수가 공급되는 경우에만 적용한다. OAuth와 정적 토큰을 불필요하게 중복 설정하지 않는다.

- [Hugging Face MCP 계정 설정](https://huggingface.co/settings/mcp)
- [Hugging Face 토큰 관리](https://huggingface.co/settings/tokens)
- [Claude 공식 커넥터 설정](https://claude.ai/settings/connectors)

## 5. 현재 검증 범위와 남은 연결

설정 문법을 확인했고, 실제 SDK와 로컬 모의 서버로 신규 테스트 **8개를 통과**했다. 초기화·도구 목록·호출 인자·인증 헤더·오류·크기/시간 제한·논문 fallback을 검사했다. 허용 도구가 하나도 없으면 실패하고 일부가 없으면 `partial`로 표시한다. 이 결과는 실제 HF 서버 연결 성공과 다르다.

실제 SDK의 `list` 요청도 실행했으나 실패했다. 일반 HTTPS 요청은 `CONNECT tunnel failed, response 403`으로 거부됐으며 실행 환경의 enforced allowlist에 `huggingface.co`가 없고 설정 초안에만 추가되어 있다. 토큰 오류로 진단하거나 새 키 발급만으로 해결된다고 안내하지 않는다.

환경 설정에서 추가한 도메인을 검토·저장하고, 도구가 요구하는 Publish 절차를 완료한 뒤 적용 상태를 다시 확인한다. 그 후 `list` → 공개 모델/데이터셋 검색 → 논문 조회 순으로 실제 호출을 검사한다. 프로젝트 설정을 읽는 Codex/Claude Code 세션도 다시 연결해 도구 노출을 확인한다.

Hugging Face MCP는 **모델·학습 자료·논문을 찾는 연구 도구**다. 제주 자체 MCP는 장소·제약 도구이고 Ollama는 모델 실행 계층이다. HF 검색 결과는 모델 라이선스·한국어 품질·도구 호출·메모리 요구와 데이터셋 이용 조건을 검토한 뒤 후보로 채택한다. 이번 연결 설정만으로 Vercel 관광 앱에 모델이 배포되거나 LAG가 재현되는 것은 아니다.

## 6. 모델이 실제로 실행되는지 확인하는 절차

MCP 연결 성공과 모델 추론 성공은 별도 판정이다. HF 검색은 후보를 찾고 조건을 확인하는 과정이며, 실제 실행은 Windows·Mac 등의 Ollama 호스트에서 검사한다. 2026-11-18 현재 Codex 클라우드는 Linux x86_64이고 Ollama 실행 파일과 로컬 11434 API를 확인할 수 없었다. 이 결과는 사용자 PC 사양이나 모델 실행 불가능을 뜻하지 않는다.

| 단계 | 확인할 내용 | 통과 기준 |
|---|---|---|
| 1. MCP 연결 | 도메인 허용 후 초기화·도구 목록·공개 검색 | 실제 조회 성공. 로컬 모의 테스트와 구분 |
| 2. 후보 선정 | 모델 카드·지원 언어·라이선스·Ollama 지원 형식/태그 | 정확한 후보와 사용 조건 기록. HF 등록 사실만으로 Ollama 호환 판정하지 않음 |
| 3. 장비·용량 | Windows/Mac RAM·GPU/VRAM 또는 통합 메모리·여유 저장공간 | 양자화·context·동시성에 맞는 적재 가능성 확인 |
| 4. 로딩·응답 | 선택 모델 다운로드 후 `ollama show`, 짧은 `ollama run`, `ollama ps` | 실제 모델 적재·응답·오류 없음. 태그·digest·양자화·실행 장비 기록 |
| 5. Agent 기능 | 한국어 지시, JSON Schema, 허용 도구 호출과 결과 반영 | 런타임 스키마 검증·올바른 도구명/인자·미확인 데이터 처리 |
| 6. 서비스 적합성 | cold/warm 응답시간·메모리·일정 제약·장애 처리 | 정한 지연·품질 기준 통과 후 제한된 서비스 연결 |

모델 후보의 현재 문서 검토 결과:

- **`llama3.2:3b`: 연결 시험 기준 후보.** Meta의 공식 Llama 3.2 지원 언어에 한국어는 포함되지 않는다. 한국어 제주 추천의 기본 모델로 검증 완료된 상태가 아니며 추가 언어 적용 조건과 실제 품질을 확인해야 한다.
- **Qwen3-4B: 한국어 제품 평가 우선 후보.** 공식 발표는 한국어를 포함한 119개 언어·방언과 Apache 2.0, 도구 호출을 명시한다. 공식 Qwen/Ollama 문서에 실행 경로는 있으나, 정확한 현재 `qwen3:4b` registry 태그·양자화·사용자 장비 추론은 아직 확인하지 못했다. 지원 언어 표기는 제주 추천 정확도 보장이 아니다.

선택한 실제 태그가 확인된 장비에서 다음 순서로 실행한다. 아래 Llama 명령은 기존 연결 시험 기준이며 제품 모델 채택 명령이 아니다.

```sh
ollama --version
ollama pull llama3.2:3b
ollama show llama3.2:3b
ollama run llama3.2:3b "한국어로 안녕이라고 한 단어로 답해줘."
ollama ps
```

단순 문장 응답은 1차 실행 검사다. `/api/chat`에 JSON Schema와 고정된 읽기 도구를 각각 전달하여 출력 형식·도구 요청을 검사하고, 도구 실행 결과를 다시 모델에 제공하는 왕복 검사까지 해야 Agent 후보로 판단할 수 있다. 실제 모델·장비를 정하기 전에는 필요한 최소 RAM이나 GPU 속도를 확정하지 않는다.

## 7. 공식 근거

- [공식 README·인증·전송](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/README.md)
- [익명 도구 목록](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/app/src/shared/settings.ts#L15)
- [읽기 preset](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/app/src/shared/bouquet-presets.ts#L32)
- [검색 스키마](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/mcp/src/repo-search.ts#L22)
- [파일 도구 스키마](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/mcp/src/hf-fs-contract.ts#L47)
- [논문 본문 fallback](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/packages/mcp/src/hf-fs-papers.ts#L1190)
- [기존 MCP 프로토콜 호환 시험](https://github.com/huggingface/hf-mcp-server/blob/16ebc9579c25266032ac2234414b9dd98fd96cf4/scripts/test-http-protocol.ts#L34)
- [Qwen3 공식 발표: 모델·한국어·라이선스·도구 호출](https://github.com/QwenLM/qwenlm.github.io/blob/1f89f8c81adb85e3808f9cc15180ab1fdacbb900/content/blog/qwen3/index.md)
- [Meta Llama 3.2 공식 모델 카드](https://github.com/meta-llama/llama-models/blob/0e0b8c519242d5833d8c11bffc1232b77ad7f301/models/llama3_2/MODEL_CARD.md)
- [Qwen3 공식 Ollama 실행 안내](https://github.com/QwenLM/Qwen3/blob/7a2f61ffc7a20d47efcd2bf97f6f2bf52729042e/README.md#L239)
