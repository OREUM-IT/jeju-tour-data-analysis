# 35. 운영 Gateway에서 Gemini와 GPT 비교 (같은 96문항)

2026-11-25 준비 기록이다. 사용자가 "96문항 평가로 공정 비교"를 골랐다. **도구를 만들고 이 저장소 작업 환경에서만 확인했다. 작성자는 AWS에서 실행하지 않았고, 실제 Gemini·OpenAI 호출도 하지 않았다.** 수집과 채점은 사용자가 아래 절차로 AWS에서 했다. 2026-11-26 Gemini 수집이 끝났고([4절](#4-gemini-수집-결과2026-11-26-사용자-제공-출력)), 2026-11-27 두 안의 채점을 마쳤다([5절](#5-두-안-비교2026-11-27-사용자-제공-출력)). 둘 다 사용자 제공 출력이다. 결과를 받지 못한 Gemini 8건은 Agent 오류로 판정돼 다시 보내지 않았다. 같은 날 사용자가 3절 6단계(GPT 다시 적용)를 실행했고 PR #46의 Gateway 변경도 AWS에 적용했다(사용자 제공 출력, [5.4절](#54-제안결정은-사용자)). `productionReady:false`다.

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

1. 문항·계획 만들기(모델·네트워크 없음). `sameAsLocalHeldOut:true`(정답 해시 `8c2d8299…bd9b`)가 나와야 한다. `planSha256`은 카탈로그 내용 해시도 묶으므로 서버마다 다를 수 있다(AWS 2026-11-25: `22817d44…`). 두 모델은 같은 서버 계획으로 재므로 비교에는 영향이 없다.
```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh prepare
```
2. (선택) 모델 없는 기준선을 AWS에서도 만든다. 2절 표와 같은 값이 나오면 서버 규칙만의 점수가 그 서버의 카탈로그에서도 같다는 확인이 된다(카탈로그 해시는 다를 수 있다). 2026-11-25 AWS에서 만든 보고서의 채점 수치는 2절과 같았고 카탈로그 해시는 달랐다([5절](#5-두-안-비교2026-11-27-사용자-제공-출력)).
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
   `failed:JOB_NOT_FOUND`나 `failed:RESULT_HIDDEN_BY_EDGE`로 끝난 문항이 있으면 6번 전에 [4절](#결과를-받지-못한-8건)대로 다시 보낼지부터 정한다.
6. **GPT로 다시 적용**. Gemini에서 결과를 받지 못한 문항이 있으면 [4절](#결과를-받지-못한-8건)의 판정 뒤에 한다. 다시 보내야 한다면 Gemini 흐름이 적용돼 있는 동안 보내야 하기 때문이다. 2026-11-27 판정으로 이번 비교에서는 다시 보낼 문항이 없어 이 보류가 풀렸고, 같은 날 사용자가 이 단계를 실행했다(사용자 제공 출력, [32 문서](32-agent-gateway-tunnel.md#비교-뒤-다시-적용2026-11-27-사용자-제공-출력)).
```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh
```
7. 공유할 집계만 출력해 붙인다. 질의·정답·장소 ID·응답 원문은 들어 있지 않다.
```bash
cd ~/oreumit-agent && cat deploy/gateway-eval-reports/gpt-report.json deploy/gateway-eval-reports/gpt-collection.json deploy/gateway-eval-reports/gemini-report.json deploy/gateway-eval-reports/gemini-collection.json
```
   토큰·호출 수는 [32 문서의 확인 SQL](32-agent-gateway-tunnel.md#지연토큰부하-측정-도구)로 함께 본다(`model` 열로 두 흐름이 나뉜다).

### 결과를 해석하지 못했거나 Cloudflare가 가렸을 때

2026-11-25 AWS 첫 시험 2건이 2~4초 만에 `unknown:RESULT_RESPONSE_INVALID`로 끝났다(사용자 제공 출력). 수집기는 Gateway 응답이 예상 형식과 다르면 다시 보내지 않고 `unknown`으로 적는다. 이제 그 줄에 `diagnostic`(HTTP 상태, 응답 항목 이름, 상태·오류 코드·동작 같은 정해진 값, 장소 수)을 함께 적고 화면에도 보인다. 장소 ID와 질의 원문은 적지 않는다. 원인을 고친 뒤 `--retry-unknown`을 붙이면 `unknown`으로 끝난 문항만 다시 보낸다(이미 한 번 실행됐을 수 있어 유료 호출이 1건씩 더 든다). 다만 `diagnostic`의 `http`가 500이고 `bodyKeys`에 `cloudflare_error`가 있는 줄은 가려진 Gateway 실패로 보고 다시 보내지 않는다([32 문서](32-agent-gateway-tunnel.md#대기실패재시작의-의미)).

```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh collect gpt --limit 2 --retry-unknown
```

결과 조회(GET)에 Gateway 오류 코드 없는 5xx(502·503·504·52x·530)가 오면 수집기는 그 답을 작업 결과로 보지 않는다. Cloudflare 같은 가장자리의 일시 오류로 보고, 제출은 다시 하지 않은 채 5초 뒤 같은 작업을 다시 조회한다. 그 횟수는 `edgeRetries`에 적는다(PR #43). 일시 오류라면 다음 조회에서 결과가 온다. 그런데 Gateway가 502·504로 저장한 결과는 Cloudflare가 자기 오류로 바꿔 답한다(Cloudflare 문서, [4절](#결과를-받지-못한-8건)). 그러면 결과 보존 300초가 지나 404 `JOB_NOT_FOUND`가 올 때까지 같은 답만 받는다. 2026-11-26 Gemini의 8건이 이렇게 끝난 것으로 본다(추정).

PR #46의 수집기는 가장자리 502·504 바로 다음에(그 사이 다른 답 없이) 404 `JOB_NOT_FOUND`가 오거나, 그 상태로 수집기의 기한(제출 뒤 720초)에 이르면 `failed:RESULT_HIDDEN_BY_EDGE`로 적고, 마지막 가장자리 응답의 HTTP 상태를 `edgeStatus`에 남긴다(구현. 2026-11-27 AWS checkout에 들어갔지만 이 수집기로 모은 기록은 아직 없다). 502·504만 보는 것은 Cloudflare가 원점의 본문을 바꾸는 상태가 이 둘이기 때문이다. 다른 가장자리 오류(503·52x·530) 뒤의 404는 `failed:JOB_NOT_FOUND` 그대로다. 410 `JOB_EXPIRED`는 실행되지 못하고 대기에서 만료된 작업에 Gateway가 직접 보인 답이라 `failed:JOB_EXPIRED` 그대로다. `RESULT_HIDDEN_BY_EDGE`는 결과를 보지 못한 채 작업이 사라졌다는 기록이며, 그 결과가 무엇이었는지는 이 기록으로 알 수 없다. 저장된 502·504가 가려진 경우와, 가장자리가 Gateway에 닿지 못하는 사이 작업이 사라진 경우(작업은 RAM에만 있어 재시작하면 사라진다)가 같은 기록이 된다. 둘은 cloudflared 로그의 원점 연결 오류로 가른다. `failed`이므로 `--retry-unknown`으로도 다시 보내지 않는다. 이미 적힌 기록은 고치지 않는다. 그래서 Gemini의 8건은 `failed:JOB_NOT_FOUND`로 남는다. 채점기는 두 코드 모두 실패(`transport_error`)로 센다. 다만 `edgeStatus` 504인 `RESULT_HIDDEN_BY_EDGE`는 `timeout`으로 센다. Gateway가 시간 초과를 504로 저장하므로 그것이 가려진 것으로 본다(추정).

## 4. Gemini 수집 결과(2026-11-26, 사용자 제공 출력)

사용자가 AWS에서 3절 5단계(Gemini 수집)를 마치고 붙여 준 출력이다. 작성자의 AWS 실행이 아니다. 흐름은 `vertex-8-call`(메시지 저장 끔)이었다. 채점 보고서는 [5절](#5-두-안-비교2026-11-27-사용자-제공-출력)에 있다. 본 실행(2026-11-25 시험 2건 뒤의 94건)의 마지막 줄 앞부분은 다음과 같다(수집기는 이 뒤에 `modelExecutionRequested`·`productionReady`도 적는다).

```json
{"status":"completed","stoppedReason":null,"outcomesThisRun":{"accepted":82,"failed:JOB_NOT_FOUND":8,"failed:RECOMMENDATION_BLOCKED":4},"submissionsThisRun":94,"casesRemaining":0}
```

| 결과 | 문항 | 내용 |
|---|---:|---|
| 수락 | 84 | 본 실행 82 + 2026-11-25 `--limit 2` 시험 2. 시험 2건은 [32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)의 11-25 Gemini 실행 2건(수락 2)과 수가 맞는다 |
| `failed:RECOMMENDATION_BLOCKED` (HTTP 422) | 4 | `CASE-044`·`063`·`092`·`094`. 최종 검증이 막은 정상 차단 답이다. 채점기는 `invalid_output`으로 센다 |
| `failed:JOB_NOT_FOUND` (HTTP 404) | 8 | `CASE-005`·`012`·`014`·`015`·`051`·`053`·`080`·`095`. 결과를 받지 못했다. Langflow 실행이 Agent 오류로 끝난 실패로 판정했고 다시 보내지 않았다([아래](#결과를-받지-못한-8건)). 채점기는 `transport_error`로 센다 |
| 합 | 96 | `casesRemaining:0` |

### 결과를 받지 못한 8건

**관측(사용자 제공 출력).** 8건은 문항마다 `edgeRetries` 59~60, `totalSeconds` 361~395였고, 마지막 조회는 404 `JOB_NOT_FOUND`였다. 이 `totalSeconds`는 작업이 끝난 뒤 결과 보관 약 300초 동안 다시 읽은 시간을 포함하므로 실행 시간이 아니다(코드 확인). `edgeRetries`는 결과 조회가 Gateway 오류 코드 없는 5xx(502·503·504·52x·530) JSON을 받아 5초 뒤 다시 조회한 횟수다(코드 확인: 수집기 클라이언트는 JSON 응답만 받아들인다). 사용자의 Cloudflare 대시보드에는 2026-11-26 11:17:42~11:18:17 KST에 한 작업의 `GET agent.oreumit.com /v1/jobs/gw-…`가 502 Bad Gateway로 기록돼 있었고, 그 뒤로는 202·200 기록이 보였다. 이 작업이 8건 가운데 하나인지는 확인하지 않았다(실패한 줄에는 작업 ID가 없다). 사용자가 cloudflared 컨테이너 로그를 찾아봤을 때 원점에 닿지 못했다는 오류 줄은 없었다.

**원인(추정, 근거가 강함).**

1. 2026-11-26 수집 당시 AWS의 Gateway(PR #46 이전 코드)는 상류 실패(Langflow·Agent 오류, 형식이 맞지 않는 최종 출력, MCP 호출 실패)를 작업 결과 HTTP 502 `GATEWAY_UNAVAILABLE`로 저장했다(`services/agent-gateway/upstream.mjs`의 `UpstreamError` 기본값, `jobs.mjs`의 `errorResult`). 끝난 결과는 300초 보관하고(`JOB_POLICY.resultTtlMs`), 그 뒤의 조회는 404 `JOB_NOT_FOUND`다.
2. Cloudflare는 원점이 502·504로 답하면 자기 502·504 오류로 바꿔 답한다([Cloudflare 문서](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-502-504/)). 원점의 502·504를 그대로 넘기는 영역 설정 `origin_error_page_pass_thru`는 Enterprise 전용이다. 2026-04-27부터는 요청이 `Accept: application/json`을 보내면 5xx 오류를 JSON(`cloudflare_error`·`retryable` 등)으로 답한다([변경 기록](https://developers.cloudflare.com/changelog/post/2026-04-27-structured-responses-for-5xx-errors/), [오류 응답 설명](https://developers.cloudflare.com/fundamentals/reference/error-responses/)).
3. 수집기(`deploy/collect-gateway-eval.py`)는 `Accept: application/json`을 보낸다. 그래서 Gateway 코드가 없는 올바른 JSON을 받았고, 이를 가장자리 일시 오류로 보아 5초 뒤 같은 결과를 다시 읽었다. 저장된 결과가 사라질 때까지 이를 되풀이했다. 59~60회 × 5초 ≈ 300초가 보관 시간과 맞는다.
4. 터널 장애로는 설명되지 않는다. 8건마다 바로 다음 문항이 곧바로 접수됐고(202), cloudflared 로그에도 원점 연결 오류가 없었다.

**판정(2026-11-27, 사용자 제공 출력).** 8건은 읽기 전용 [지연·토큰 집계 SQL](32-agent-gateway-tunnel.md#지연토큰-집계-sql) [2]의 `2026-11-26`·`gemini-3.8-flash` 행으로 가르기로 했다. 사용자가 그 SQL을 다시 실행해 붙여 줬다([32 문서의 (다)](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)). 그 행은 `runs` 94·`accepted` 82·`blocked` 4·`agent_error` 8·`no_final` 0이었다. `runs`가 본 실행의 제출 수 94와 같아 이날 다른 Gemini 실행은 섞이지 않은 것으로 본다(이 SQL은 웹 요청과 합성 요청을 나누지 못한다). 값은 아래 표의 첫 줄과 정확히 맞는다. 그래서 8건은 Langflow 실행이 Agent 오류로 끝난 모델·Agent 쪽 실패이며, 다시 보내지 않고 실패로 센다. Gemini 결과는 수락 84·차단 4·Agent 오류 8로 정해졌다. 이날 실패한 모델 호출이 5회이고 가장 긴 실행이 90.1초여서, 8건은 모델 호출 오류와 실행 상한 90초가 섞인 것으로 본다(추정. 이 SQL은 실행별로 나누지 못한다). Gateway가 상류 실패를 502 결과로 저장하고 Cloudflare가 가렸다는 위 원인과도 어긋나지 않는다.

| 그 행의 값 | 뜻 | 8건(제안) | 2026-11-27 |
|---|---|---|---|
| `accepted` 82·`blocked` 4, `agent_error`+`no_final` 8 | 8건의 Langflow 실행이 최종 검증까지 가지 못했다. 모델·Agent 쪽 실패다 | 다시 보내지 않는다 | **이 줄**(`agent_error` 8·`no_final` 0) |
| `accepted` 90·`blocked` 4, `agent_error`+`no_final` 0 | Langflow 실행은 흐름 안 최종 검증을 통과했고, 그 뒤 Gateway 단계(MCP 재검증 호출 등)가 실패했다. 인프라 실패다 | Gemini 흐름이 적용돼 있는 동안 다시 보낸다 | |
| 그 밖(둘이 섞이거나 `runs`가 94가 아님) | 이 SQL은 세션 ID를 내지 않아 문항별로 나눌 수 없다 | 다시 보내기 전에 따로 정한다 | |

**비교 규칙(제안).**

- 모델·Agent 실패라면 8건은 다시 보내지 않고 실패로 센다(채점기는 `transport_error`로 센다). 2026-11-27 판정이 이 경우다. 비교하는 빌드에서 난 실패는 다시 보내지 않는다는 규칙이다. GPT 수집에서도 `failed`로 끝난 문항은 다시 보내지 않았다. Gemini의 실패만 다시 보내면 Gemini에만 시도를 더 주게 된다.
- GPT 시험 2건을 다시 보낸 것은 이 규칙에 어긋나지 않는다. 이 2건은 비교하는 빌드가 아니라 바뀌기 전 빌드(`rule-action-low`)에서 OpenAI 400(`reasoning_effort`) Agent 오류와 같은 때 `unknown`으로 끝났다([32 문서](32-agent-gateway-tunnel.md#openai-400-reasoning_effort-수정2026-11-25), 다른 세션 기록). 고친 빌드(PR #41)를 적용한 뒤 다시 보내 수락됐다(사용자 제공 출력, 다른 세션 기록).
- SQL이 인프라 실패를 보일 때만, Gemini 흐름이 적용돼 있는 동안 다시 보내기로 했다. 그래서 3절 6단계(GPT 다시 적용, `apply-agent-rule-action.sh`)를 이 판정 뒤로 미뤘다. 판정이 모델·Agent 실패여서 다시 보낼 문항이 없고, 이 보류는 풀렸다(2026-11-27). 같은 날 사용자가 GPT를 다시 적용했다(사용자 제공 출력, [32 문서](32-agent-gateway-tunnel.md#비교-뒤-다시-적용2026-11-27-사용자-제공-출력)).
- 수집기에는 `failed`로 끝난 문항을 다시 보내는 선택지가 없다(`--retry-unknown`은 `unknown`만 다시 보낸다). 이번에는 필요 없었다.
- GPT 수집의 32번(사용자 제공 출력, 다른 세션 기록). 본 실행에서 제출 0.14초 뒤의 첫 결과 조회가 Cloudflare 502 JSON(`cloudflare_error`·`retryable`, Gateway 코드 없음)을 받아 `unknown:RESULT_RESPONSE_INVALID`(`QUEUE_MODEL_CHECK_INVALID`)로 끝났다(커밋 `9620068`). 0.14초는 GPT 실행이 끝나기에 너무 짧다(Langflow 실행 p50 2.1초). 그래서 저장된 결과가 가려진 것이 아니라 지나가는 가장자리 오류였을 가능성이 크다(추정. 0.14초 안에 Gateway가 Langflow 앞에서 실패했을 가능성은 기록으로 배제하지 못한다). 이 1건만 `--retry-unknown --limit 1`로 다시 보내 수락됐다(6.39초). 그래서 GPT 결과는 수락 81·차단 15다. 첫 채점(다시 보내기 전)은 수락 80·차단 15·`unknown` 1이었고, [5절](#5-두-안-비교2026-11-27-사용자-제공-출력)의 2026-11-27 채점은 다시 보낸 32번을 포함한다. 11-25 GPT 실행 가운데 최종 검증까지 간 97건(수락 82, [32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력))은 수집 96줄(수락 81)보다 1건 많다. 32번의 첫 시도도 끝까지 실행돼 수락됐다고 보면 수가 맞는다(추정, 웹 요청 같은 비교 밖 실행이 섞였다면 달라진다).

**수정(구현, PR #46 병합).** `claude/gateway-edge-hidden-failure`([PR #46](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/46))는 Gateway가 상류 실패를 500으로 답하게 했다([32 문서](32-agent-gateway-tunnel.md#대기실패재시작의-의미)). 수집기는 가려진 결과를 `failed:RESULT_HIDDEN_BY_EDGE`로 적는다([3절](#결과를-해석하지-못했거나-cloudflare가-가렸을-때)). 채점기는 결과를 보지 못한 줄(`JOB_NOT_FOUND`·`RESULT_HIDDEN_BY_EDGE`), 실행되지 않은 대기 만료(`JOB_EXPIRED`), `unknown` 줄의 초를 수집 보고서의 시간 집계(`timingSeconds`)에서 뺀다. 이전 채점기로는 이 8건의 361~395초가 `totalSeconds`·`runSeconds` 집계에 들어가 p95와 최대를 정한다(96건 중 8.3%로 상위 5%보다 많다). 채점 단계는 checkout의 스크립트를 쓰므로(`run-gateway-eval.sh`) 이미지를 다시 빌드하지 않아도 이 변경을 쓴다. 2026-11-27 사용자가 AWS에서 `main@26029b2`(PR #46 병합)를 받아 다시 채점했다(사용자 제공 출력). 그래서 [5절](#5-두-안-비교2026-11-27-사용자-제공-출력)의 Gemini 시간 집계는 결과를 본 88줄만 센다. 이 비교를 마치는 데 Gateway 적용은 필요 없었다. 채점 뒤 같은 날 사용자가 `apply-agent-queue.sh`로 Gateway 변경(상류 실패 500)을 AWS에 적용했다(사용자 제공 출력, [32 문서](32-agent-gateway-tunnel.md#대기실패재시작의-의미)).

## 5. 두 안 비교(2026-11-27, 사용자 제공 출력)

2026-11-27 사용자가 AWS에서 `main@26029b2`(PR #46 병합)를 받고, GPT·Gemini의 채점 결과만 지운 뒤 같은 채점기로 두 안을 다시 채점해 붙여 줬다. 작성자의 AWS 실행이 아니다. 채점 출력의 결과 수는 GPT `accepted` 81·`failed:RECOMMENDATION_BLOCKED` 15, Gemini `accepted` 84·`failed:JOB_NOT_FOUND` 8·`failed:RECOMMENDATION_BLOCKED` 4였다. 세 보고서는 같은 서버 계획(`planSha256` `22817d44…`)과 정답(`8c2d8299…bd9b`)을 묶는다. 모델 없는 기준선은 2026-11-25 AWS에서 만든 보고서가 있어 다시 만들지 않았다. 그 채점 수치(`groups`)는 [2절](#2-모델-없이-서버만으로-나오는-점수-기준선)의 로컬 보고서와 모두 같다(작성자가 옮겨 적은 파일로 비교). 묶은 해시 가운데서는 카탈로그 해시(`catalogSha256`, 읽은 JSON의 정규화 해시)만 다르고, 그것을 묶는 `planSha256`·`runSha256`과 실행 시각도 다르다. 카탈로그 내용이 어딘가 다르다는 뜻이며, 어디가 다른지는 보고서로 알 수 없다. 서버 규칙만의 점수가 AWS 카탈로그에서도 같다는 확인이고, 카탈로그가 같다는 확인은 아니다.

**운영 구성 두 가지의 비교다.** 흐름 구조와 모델이 함께 다르다([1절](#1-무엇을-비교하나)). 아래 차이를 모델 우열로 읽지 않는다.

| 안 | 채점 보고서 | 수집 보고서 |
|---|---|---|
| 모델 없음 기준선(AWS, 2026-11-25) | [채점](../reports/gateway-eval-heldout-no-model-reference-aws-20261125.json) | [수집](../reports/gateway-eval-heldout-no-model-reference-aws-collection-20261125.json) |
| GPT 안(`gpt-5.4-mini`, `rule-action`, 2026-11-25 수집) | [채점](../reports/gateway-eval-heldout-gpt-5.4-mini-rule-action-20261125.json) | [수집](../reports/gateway-eval-heldout-gpt-5.4-mini-rule-action-collection-20261125.json) |
| Gemini 안(`gemini-3.8-flash`, `vertex-8-call`, 2026-11-25~26 수집) | [채점](../reports/gateway-eval-heldout-gemini-3.8-flash-vertex-8-call-20261126.json) | [수집](../reports/gateway-eval-heldout-gemini-3.8-flash-vertex-8-call-collection-20261126.json) |

보고서 이름은 기존 로컬 기준선과 같은 `gateway-eval-heldout-<이름>[-collection]-<UTC 완료 날짜>.json` 형식이다. 이름 자리에는 채점 명령에 넘긴 모델 ID와 흐름 이름을 점까지 그대로 썼다(수집 보고서의 `model`·`flow`와 같다).

### 5.1 품질

| 지표 | 모델 없음 기준선 | GPT 안 `rule-action` | Gemini 안 `vertex-8-call` |
|---|---|---|---|
| 응답(차단·오류 아님) | 96/96 | 81/96 (차단 15) | 84/96 (차단 4·Agent 오류 8) |
| 전체 96문항 동작 정답 | 87/96 (0.906) | 72/96 (0.750) | 77/96 (0.802) |
| 답한 문항의 동작 정답 | 87/96 (0.906) | 72/81 (0.889) | 77/84 (0.917) |
| 허용 안 된 ID | 0.186 (49/264) | 0.043 (15/349) | 0.012 (4/346) |
| 허용 안 된 ID가 있는 문항 | 19/96 | 6/81 | 2/84 |
| 사유: 후보 밖·걷기 최소화에 경로(나머지 사유 0) | 43·6 | 5·10 | 3·1 |
| P@5 (채점 문항) | 0.710 (77) | 0.842 (62) | 0.928 (67) |
| R@5 (채점 문항) | 0.505 (77) | 0.897 (62) | 0.956 (67) |
| 페르소나 테마 일치 (채점 문항) | 0.599 (80) | 0.681 (65) | 0.693 (70) |
| 확인 안내 제공률 | 0.667 (32/48) | 0.595 (22/37) | 0.725 (29/40) |
| 확인 주제 포함 (채점 문항) | 0.604 (48) | 0.527 (37) | 0.650 (40) |
| 필수 조건 오판 (해당 문항) | 0 (32) | 0 (22) | 0 (29) |
| 예상 밖 `impossible` (잰 문항) | 0 (88) | 0 (73) | 0 (77) |
| 답 1개당 장소 수 | 2.75 | 4.31 | 4.12 |

- 실패를 오답으로 세는 것은 전체 96문항 동작 정답(`actionAccuracyAllCases`)뿐이다. 나머지 지표는 각 안이 답한 문항에서만 잰다. 답한 문항이 다르고(GPT 81·Gemini 84) 지표마다 채점 문항 수(괄호)도 달라 분모가 다르다. 같은 문항끼리 짝지어 견준 비교는 하지 않았다.
- 개수(87/96, 49/264 등)와 답 1개당 장소 수는 작성자가 보고서의 비율·개수로 계산했다.
- 기준선은 후보 앞 3곳만 넘긴다. 그래서 R@5가 구조적으로 낮다(정답이 5곳 이상인 문항에서는 많아야 0.6).
- 세 보고서 모두 사람 검수 0, 보안 평가 미실행(`securityEvaluation: not_executed`), 비용 미측정(`costUsd` `null`)이다.

### 5.2 속도·호출·토큰

| 지표 | GPT 안 | Gemini 안 | 출처 |
|---|---|---|---|
| 수집기 전체 시간 p50·p95·최대(초) | 4.32·6.41·8.48 (96줄) | 18.77·60.6·70.96 (88줄) | 수집 보고서 `totalSeconds` |
| Langflow 실행 p50·p95·최대(ms) | 2146·4172·6079 (100건) | 18719·69477·90145 (94건) | 지연·토큰 SQL [2]. GPT 11-25·Gemini 11-26 행 |
| 실행당 모델 호출 평균(최대) | 2.35 (5) | 4.16 (8) | [3] |
| 모델 호출 1회 p50·p95(ms) | 907·1609 (235회) | 3128·18875 (660회) | [4] |
| 실행당 도구 호출 평균 | 1.37 | 3.20 | [3] |
| 실행당 입력·출력 토큰 | 기록 없음(235회 모두) | 29174·1321 (토큰 없는 호출 6) | [3] |
| 실패한 모델 호출 | 3 (고치기 전 빌드의 HTTP 400) | 5 | [3] |
| 비용 | 계산하지 않음 | 계산하지 않음 | GPT 토큰이 기록되지 않았고, 단가가 저장소에 없다 |

SQL 수치는 [32 문서의 (나)·(다)](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)다(사용자 제공 출력).

- 수집기 시간은 제출부터 결과를 받을 때까지다. Gateway 큐·Cloudflare 왕복이 들어 있고, 2초마다 조회하므로 최대 2초쯤 길게 잡힌다. 큐 대기 p50은 0.08초·0.07초였다. Langflow 실행 시간은 흐름 안만 잰다.
- 두 시간은 대상 실행이 다르다. SQL의 GPT 행 100건에는 고치기 전 빌드의 1초짜리 실패 3건이 들어 있다. Gemini 11-26 행 94건에는 Agent 오류 8건이 들어 있지만, 수집기 시간(88줄)은 결과를 보지 못한 그 8줄을 뺀다(PR #46 채점기).
- [4]의 호출 1회 시간은 날짜를 나누지 않는다. Gemini는 11-19부터의 660회(11-26은 391회), GPT는 11-25의 235회다.

### 5.3 읽는 법

- **장소 선택(관측).** 두 안 모두 기준선보다 P@5·R@5·허용 안 된 ID·페르소나 일치가 크게 낫다. 모델 몫이 있다는 뜻이다([2절](#2-모델-없이-서버만으로-나오는-점수-기준선)). R@5의 차이에는 기준선이 3곳만 넘기는 구조도 들어 있다.
- **답한 문항끼리(관측, 우연 여부는 추정).** Gemini 안이 P@5(0.928 대 0.842)·R@5(0.956 대 0.897)·허용 안 된 ID(0.012 대 0.043)에서 앞선다. 짝짓지 않고 문항별 흩어짐을 가정한 거친 계산(작성자, 정식 검정 아님)으로는 P@5 차이는 우연을 넘을 가능성이 높고 R@5는 경계다. 허용 안 된 ID는 ID 단위로 세면 우연을 넘어 보이지만, 위반이 GPT 6문항·Gemini 2문항에 몰려 있어 문항 단위로는 우연 범위다. 페르소나 일치는 비슷하다(0.693 대 0.681). 확인 안내 제공률은 Gemini 안이 높지만(0.725 대 0.595) 이 표본에서는 우연 범위다. 답한 문항의 동작 정답과 응답 수의 차이도 우연 범위다.
- **전체 96문항(관측).** 실패가 동작 정답을 좌우한다. 기준선 0.906, Gemini 안 0.802, GPT 안 0.750이다. 두 안의 차이(5/96)는 이 표본에서 우연 범위다(추정). AI 결과가 없는 문항은 GPT 15(모두 최종 검증 차단)·Gemini 12(차단 4·Agent 오류 8)로 비슷하다.
- **GPT 안의 허용 안 된 ID(관측·추정).** 15개 중 10개가 걷기 최소화 요청에 낸 경로(`routeForMinimalWalk`)다. 운영 조립기는 걷기를 문맥 조건으로만 받고 경로를 빼는 규칙이 없다(코드 확인). [33 문서 2.1절](33-huggingface-models-evaluation.md#21-고도화-실험-동작은-서버-규칙-모델은-장소-선택만-2026-11-22)이 적은 규칙 차이(걷기 최소화 경로 제외)와 같은 것으로 본다(추정). 기준선에도 6개가 있다. 이 규칙을 고친다면 이 96문항이 아니라 새 시드의 세 번째 세트로 확인한다.
- **되묻기.** 운영 서버 조립기는 `ask_clarification`을 만들지 않으므로, 이 세트의 되묻기 8문항은 GPT 안이 맞힐 수 없다([2절](#2-모델-없이-서버만으로-나오는-점수-기준선)).
- **속도(관측·추정).** 수집기 기준 GPT 안이 p50 약 4배(4.32초 대 18.77초), p95 약 9배(6.41초 대 60.6초) 빠르다. 호출 1회가 빠르고(p50 907 대 3128 ms. Gemini 값은 11-19부터의 660회 전체이고 비교일만의 값은 없다) 실행당 호출이 적은(2.35 대 4.16회, 약 1.8배) 두 가지가 겹친 것으로 본다(추정). 이 차이는 우연으로 설명할 수 없을 만큼 크다.

### 5.4 제안(결정은 사용자)

- 어느 안을 운영에 둘지는 맞바꿈이다. GPT 안은 빠르고, 비교한 빌드에서 Agent 오류가 없었다. Gemini 안은 답한 문항의 P@5가 높다(허용 안 된 ID도 적지만 문항 단위로는 우연 범위다. 둘 다 짝짓지 않은 비교다). AI 결과가 없는 비율은 비슷하다(15.6% 대 12.5%).
- 작성자 제안: GPT 안을 운영에 다시 적용하고(3절 6단계), 차단 15건과 걷기 최소화 경로 규칙을 손본다. 규칙을 바꾸면 새 시드의 세 번째 세트로 확인한다.
- **2026-11-27 비교 뒤 적용(사용자 제공 출력).** 사용자가 3절 6단계대로 GPT 안을 다시 적용했다(`agent-rule-action-verified`, 메시지 저장 끔, [32 문서](32-agent-gateway-tunnel.md#비교-뒤-다시-적용2026-11-27-사용자-제공-출력)). 이어 PR #46의 Gateway 변경(상류 실패를 500으로)도 적용했다(`agent-queue-applied`, [32 문서](32-agent-gateway-tunnel.md#대기실패재시작의-의미)). 두 적용 모두 모델을 실행하지 않았다. 웹 추천은 지금 GPT 안으로 동작한다. 운영 준비 완료가 아니며 `productionReady:false`다. 다음은 로그인 웹 추천이 최종 검증까지 간 뒤 [적용 뒤 확인 SQL](32-agent-gateway-tunnel.md#적용-뒤-확인-sql)로 이 빌드의 메시지 저장 끔을 직접 판정하는 것과, 실제 웹 실행 20건 이상의 차단·Agent 오류 비율이다. 실패가 Cloudflare를 지나도 Gateway 코드가 남는지는 실제 실패 작업이 생긴 뒤 확인한다.
- Gemini 안을 고른다면 Agent 오류(96문항 중 8, 8%)와 느린 응답을 먼저 줄여야 한다. 수집기 p95 약 60초는 결과를 본 88줄의 값이고, Agent 오류 8건을 넣은 Langflow 실행 p95는 69.5초다.
- 다음 측정:
  - 같은 문항끼리 짝지은 비교. 문항별 결과는 AWS의 `deploy/.local/gateway-eval/`에만 있다. 거기서 짝지은 집계만 내는 작은 도구를 AWS에서 돌려야 한다(아직 만들지 않음).
  - GPT 토큰 기록. 규칙 동작 클라이언트에 `stream_usage=True`를 더하는 수정은 [32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)에서 제안만 했고 구현하지 않았다. 기록이 생겨야 두 안의 비용을 견줄 수 있다.
  - 사람 검수(아직 0).

### 5.5 옮겨 적은 보고서

사용자가 붙여 준 보고서 6개에는 Lightsail 웹 콘솔이 JSON 값 바깥에 끼워 넣은 표시 잡음(앞 명령의 조각 등)이 있었다. 값 자체는 온전했다. 작성자가 잡음을 빼고 채점기가 쓰는 형식(`JSON.stringify(값, null, 2)`와 끝 줄바꿈)으로 옮겨 적었다. 사용자가 AWS에서 아래 명령으로 같은 해시를 확인하기 전까지 `reports/`의 6개는 옮겨 적은 사본이다.

```bash
cd ~/oreumit-agent && sha256sum deploy/gateway-eval-reports/{reference,gpt,gemini}-{report,collection}.json
```

옮겨 적은 사본의 해시다(같은 순서).

```text
1ffc620e14c2d732b32a85bd73b5c2b8800f642da290a6f63cebbcd8ca3ee53a  deploy/gateway-eval-reports/reference-report.json
84f3af4e38d917a0363434de295cc78b932c3f54d0a40adf0b3b416143434bc7  deploy/gateway-eval-reports/reference-collection.json
df9bb9c61196840257ed839b77268763d8b00966eb19cb18d7967b5dee9e6e82  deploy/gateway-eval-reports/gpt-report.json
6eecbbe81b1e4a1920e7547ada47cc674b2b29b4bcf9c79075d4f324bce630a0  deploy/gateway-eval-reports/gpt-collection.json
83103e3e11997ba589ed40d77a75d5c38d95f6204e399ff5f6ea3974a0a3ea1c  deploy/gateway-eval-reports/gemini-report.json
9c9c73280bb4e67f1e4d3b833030964100e7dafff2b45be9f53d8ef02d4a87eb  deploy/gateway-eval-reports/gemini-collection.json
```

CI에는 `gateway-eval` 보고서를 보는 검사가 아직 없다. 작성자가 저장소 검사기의 외부 집계 검사(`scripts/check_repository.py`의 `validate_external_baseline_summary`)를 메모리에서만 고쳐(`gateway` 시스템 허용, 새 세트의 정답·명세 해시) 옮겨 적은 채점 보고서 3개와 2절의 로컬 기준선에 돌렸고, 모두 통과했다. 이 검사는 정해진 키만 있는지, 자유 문장이 없는지, 개수와 비율이 서로 맞는지를 본다. 수집 보고서 3개는 이 검사의 대상이 아니다.

## 6. 남는 것과 지우는 것

- `deploy/.local/gateway-eval/`(0700, root): 문항·정답·요청·응답 원문·세부 채점. Git에 넣지 않는다. 비교가 끝나면 사용자가 지워도 된다. 다만 짝지은 비교(5.4절)를 하려면 그때까지 남겨 둔다(제안).
- `deploy/gateway-eval-reports/`: 공유 가능한 집계(채점 요약·수집 결과 수·시간). Git에는 이 저장소의 `reports/`로 옮겨 넣는다. 2026-11-27 세 안의 보고서 6개를 `reports/`에 넣었다([5절](#5-두-안-비교2026-11-27-사용자-제공-출력)의 표, 옮겨 적은 사본). 이름은 `gateway-eval-heldout-`로 시작하며, 같은 접두사의 로컬 기준선 2개(2절)와는 별개다.
- Langflow DB에는 운영 요청처럼 `gw-` trace가 남는다. 메시지는 저장되지 않는다. 정리는 [32 문서](32-agent-gateway-tunnel.md#langflow-메시지-저장-끄기와-오래된-원문-정리)의 도구를 따른다.

## 7. 도구

| 파일 | 역할 |
|---|---|
| `services/jeju-mcp/scripts/gateway-eval.mjs` | `prepare`(새 96문항·계획), `requests`(Gateway 요청), `reference`(모델 없는 기준선), `score`(가져오기·채점). 네트워크를 쓰지 않는다 |
| `deploy/collect-gateway-eval.py` | 서명 요청으로 Gateway에 1건씩 보내고 결과를 `.local`에 이어 쓴다. `--execute-model` 없이는 아무것도 보내지 않는다 |
| `deploy/run-gateway-eval.sh` | 위 둘을 묶는다. Node 단계는 이미 빌드된 `jeju-mcp` 이미지의 일회용 컨테이너에서 네트워크 없이, 저장소 코드를 읽기 전용으로 붙여 실행한다 |
| 평가 틀 | `gateway` 시스템 추가(`baseline-adapters.mjs`의 `adaptGatewayResponse`). Gateway의 `gw-` ID를 하네스의 실행 ID로 바꾸는 것 말고는 응답을 고치지 않는다 |

확인: `services/jeju-mcp` 테스트 265개(새 5개 포함), deploy 테스트 237개 중 새 6개 포함 236개 통과(나머지 1개는 이 작업 환경의 git 제한으로 main에서도 실패하는 `test_export_catalog`). 컨테이너와 같은 경로 배치(`/app`·`/data`·`/reports`)에서 `prepare`→`requests`→가짜 Gateway 결과→`score`를 끝까지 돌려 확인했다. 실제 Docker·AWS·모델 실행은 하지 않았다.
