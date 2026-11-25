# 35. 운영 Gateway에서 Gemini와 GPT 비교 (같은 96문항)

2026-11-25 준비 기록이다. 사용자가 "96문항 평가로 공정 비교"를 골랐다. **도구를 만들고 이 저장소 작업 환경에서만 확인했다. AWS에서 실행하지 않았고, 실제 Gemini·OpenAI 호출도 하지 않았다.** 비교 수치는 사용자가 아래 절차를 실행해 보고서를 붙여 준 뒤에 생긴다.

## 1. 무엇을 비교하나

| 항목 | 내용 |
|---|---|
| 문항 | Qwen 고도화 확인에 쓴 새 96문항(공개 명세에서 시드만 바꾼 세트, [33 문서 2.1절](33-huggingface-models-evaluation.md)). gold `8c2d8299…bd9b`, plan `cfc257de…f0f`. 두 흐름의 설계에 쓰지 않은 문항이다 |
| 경로 | 웹 앱과 같은 운영 경로: 서명 요청 → Cloudflare → `agent-gateway` 큐 → Langflow 흐름 → MCP 최종 검증. 요청 형식도 웹과 같은 7개 필드다 |
| Gemini | `vertex-8-call` 흐름(메시지 저장 끔). 모델이 도구로 찾고 **동작·장소·조건 판정 JSON을 직접 쓴다**. 최대 8회 호출 |
| GPT | `rule-action` 흐름(메시지 저장 끔). 모델이 도구로 찾고 **장소 ID만 고른다**. 동작·판정·안내는 서버가 근거로 조립한다 |
| 채점 | 기존 채점기(`evaluate-baselines.mjs`) 그대로. 정답은 규칙으로만 계산한다 |

**이것은 모델 단독 비교가 아니라 "운영 구성 두 가지"의 비교다.** 모델과 흐름 구조가 함께 다르다. "GPT로 바꾼 지금 구성이 이전 Gemini 구성보다 나은가"에는 답하지만, "GPT 모델이 Gemini 모델보다 낫다"고 주장하지 않는다.

## 2. 모델 없이 서버만으로 나오는 점수 (기준선)

GPT 흐름의 동작 정답은 대부분 서버 규칙이 만든다. 그래서 같은 96문항에 **모델 없이 후보 앞 3곳을 그대로 넘긴 결과**를 먼저 쟀다(오프라인, `gateway-eval.mjs reference`). [보고서](../reports/gateway-eval-heldout-no-model-reference-20261125.json)

| 지표 | 모델 없음(앞 3곳) | 참고: Qwen v2(로컬 규칙 동작) |
|---|---|---|
| 응답(차단 아님) | 96/96 | 96/96 |
| 동작 정답 | 0.906 | 0.990 |
| 허용 안 된 ID 비율 | 0.186 | 0.080 |
| P@5 / R@5 | 0.710 / 0.505 | 0.803 / 0.847 |
| 페르소나 테마 일치 | 0.599 | 0.686 |
| 확인 안내 제공률 | 0.667 | 0.667 |

읽는 법:
- GPT 흐름의 동작 정답이 0.9 근처라면 대부분 서버 몫이다. **모델의 몫은 허용 안 된 ID 비율, P@5·R@5, 페르소나 일치에서 이 기준선보다 얼마나 나은지로 본다.**
- 운영 서버 조립기는 `ask_clarification`(날짜 충돌 등 되묻기)을 만들지 않는다. 이 세트의 해당 8문항은 GPT 흐름이 맞힐 수 없다. Qwen v2의 0.990은 로컬 실험 규칙이 되묻기까지 판단했기 때문이며 운영 서버와 다르다.
- Gemini 흐름은 모델이 동작을 직접 쓰므로 되묻기도 할 수 있다. 대신 최종 검증에 막히면 응답이 없는 실패로 센다.

## 3. 실행 절차 (사용자가 AWS에서)

요금이 든다. 실행 전 확인할 것:
- 호출 규모: 2026-11-22 Gemini 8회 흐름은 실행당 평균 모델 호출 7.7회, 입력 토큰 약 8.8만 개였다. 96문항이면 입력 약 840만 토큰 규모다. GPT 흐름의 토큰은 아직 관측이 없다. 단가는 저장소에 없어 금액은 계산하지 않았다.
- 시간: 한 시간에 자기 요청을 최대 45건만 보낸다(Gateway 전체 한도 60건 중 15건은 웹 사용자 몫으로 남김). 모델마다 약 2시간 10분 이상 걸린다.
- 그동안 웹 추천은 그때 적용된 흐름으로 동작한다. 수집은 한 번에 1건씩이라 웹 요청이 들어올 자리가 남는다.
- 요청이 Gateway에서 확실히 거절된 경우(한도·혼잡)만 기다렸다 다시 보낸다. 응답이 애매하면 다시 보내지 않고 "unknown"으로 기록한다.
- 중간에 멈춰도 같은 명령을 다시 실행하면 남은 문항부터 이어 간다. 단, Ctrl+C로 끊은 순간 처리 중이던 1건은 다시 보내진다(유료 호출 1건 추가).

Langflow 로그인을 쓰는 적용·되돌리기 명령 사이에는 1분쯤 둔다(1분 5회 제한).

```bash
cd ~/oreumit-agent && git fetch origin refs/heads/main:refs/remotes/origin/main && git merge --ff-only origin/main
```

1. 문항·계획 만들기(모델·네트워크 없음). `sameAsLocalHeldOut:true`(정답 해시 `8c2d8299…bd9b`)가 나와야 한다. `planSha256`은 카탈로그 파일 해시도 묶으므로 서버마다 다를 수 있다(AWS 2026-11-25: `22817d44…`). 두 모델은 같은 서버 계획으로 재므로 비교에는 영향이 없다.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh prepare
```
2. (선택) 모델 없는 기준선을 AWS에서도 만든다. 2절 표와 같은 값이 나오면 카탈로그가 같다는 확인이 된다.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh reference
```
3. **GPT 수집** (지금 적용된 흐름). 먼저 2건만 시험한다.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh requests gpt
```
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh collect gpt --limit 2
```
   이상이 없으면 나머지를 백그라운드로 돌린다. 진행은 로그 끝을 본다(문항 번호·결과·동작·초만 기록된다).
```bash
cd ~/oreumit-agent && sudo sh -c 'nohup bash deploy/run-gateway-eval.sh collect gpt >> deploy/.local/gateway-eval/gpt-collect.log 2>&1 &'
```
```bash
cd ~/oreumit-agent && sudo tail -n 3 deploy/.local/gateway-eval/gpt-collect.log
```
   마지막 줄이 `"status":"completed"`이면 채점한다.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh score gpt gpt-5.4-mini rule-action
```
4. **Gemini로 되돌리기**(같은 흐름 ID, 메시지 저장 끔 유지).
```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh --rollback
```
5. **Gemini 수집·채점**: 3번과 같고 이름만 `gemini`.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh requests gemini
```
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh collect gemini --limit 2
```
```bash
cd ~/oreumit-agent && sudo sh -c 'nohup bash deploy/run-gateway-eval.sh collect gemini >> deploy/.local/gateway-eval/gemini-collect.log 2>&1 &'
```
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh score gemini gemini-3.8-flash vertex-8-call
```
6. **GPT로 다시 적용**.
```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh
```
7. 공유할 집계만 출력해 붙인다. 질의·정답·장소 ID·응답 원문은 들어 있지 않다.
```bash
cd ~/oreumit-agent && cat deploy/gateway-eval-reports/gpt-report.json deploy/gateway-eval-reports/gpt-collection.json deploy/gateway-eval-reports/gemini-report.json deploy/gateway-eval-reports/gemini-collection.json
```
   토큰·호출 수는 [32 문서의 확인 SQL](32-agent-gateway-tunnel.md#지연토큰부하-측정-도구)로 함께 본다(`model` 열로 두 흐름이 나뉜다).

### 결과를 해석하지 못했을 때 (`unknown:RESULT_RESPONSE_INVALID`)

2026-11-25 AWS 첫 시험 2건이 2~4초 만에 이 결과로 끝났다(사용자 제공 출력). 수집기는 Gateway 응답이 예상 형식과 다르면 다시 보내지 않고 `unknown`으로 적는다. 이제 그 줄에 `diagnostic`(HTTP 상태, 응답 항목 이름, 상태·오류 코드·동작 같은 정해진 값, 장소 수)을 함께 적고 화면에도 보인다. 장소 ID와 질의 원문은 적지 않는다. 원인을 고친 뒤 `--retry-unknown`을 붙이면 `unknown`으로 끝난 문항만 다시 보낸다(이미 한 번 실행됐을 수 있어 유료 호출이 1건씩 더 든다).

```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh collect gpt --limit 2 --retry-unknown
```

## 4. 남는 것과 지우는 것

- `deploy/.local/gateway-eval/`(0700, root): 문항·정답·요청·응답 원문·세부 채점. Git에 넣지 않는다. 비교가 끝나면 사용자가 지워도 된다.
- `deploy/gateway-eval-reports/`: 공유 가능한 집계(채점 요약·수집 결과 수·시간). Git에는 이 저장소의 `reports/`로 옮겨 넣는다.
- Langflow DB에는 운영 요청처럼 `gw-` trace가 남는다. 메시지는 저장되지 않는다. 정리는 [32 문서](32-agent-gateway-tunnel.md#langflow-메시지-저장-끄기와-오래된-원문-정리)의 도구를 따른다.

## 5. 도구

| 파일 | 역할 |
|---|---|
| `services/jeju-mcp/scripts/gateway-eval.mjs` | `prepare`(새 96문항·계획), `requests`(Gateway 요청), `reference`(모델 없는 기준선), `score`(가져오기·채점). 네트워크를 쓰지 않는다 |
| `deploy/collect-gateway-eval.py` | 서명 요청으로 Gateway에 1건씩 보내고 결과를 `.local`에 이어 쓴다. `--execute-model` 없이는 아무것도 보내지 않는다 |
| `deploy/run-gateway-eval.sh` | 위 둘을 묶는다. Node 단계는 이미 빌드된 `jeju-mcp` 이미지의 일회용 컨테이너에서 네트워크 없이, 저장소 코드를 읽기 전용으로 붙여 실행한다 |
| 평가 틀 | `gateway` 시스템 추가(`baseline-adapters.mjs`의 `adaptGatewayResponse`). Gateway의 `gw-` ID를 하네스의 실행 ID로 바꾸는 것 말고는 응답을 고치지 않는다 |

확인: `services/jeju-mcp` 테스트 265개(새 5개 포함), deploy 테스트 237개 중 새 6개 포함 236개 통과(나머지 1개는 이 작업 환경의 git 제한으로 main에서도 실패하는 `test_export_catalog`). 컨테이너와 같은 경로 배치(`/app`·`/data`·`/reports`)에서 `prepare`→`requests`→가짜 Gateway 결과→`score`를 끝까지 돌려 확인했다. 실제 Docker·AWS·모델 실행은 하지 않았다.
