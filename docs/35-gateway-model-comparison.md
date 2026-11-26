# 35. 운영 Gateway에서 Gemini와 GPT 비교 (같은 96문항)

2026-11-25 준비 기록이다. 사용자가 "96문항 평가로 공정 비교"를 골랐다. **도구를 만들고 이 저장소 작업 환경에서만 확인했다. AWS에서 실행하지 않았고, 실제 Gemini·OpenAI 호출도 하지 않았다.** 비교 수치는 사용자가 아래 절차를 실행해 보고서를 붙여 준 뒤에 생긴다. 2026-11-26 Gemini 수집이 끝났다([4절](#4-gemini-수집-결과2026-11-26-사용자-제공-출력), 사용자 제공 출력). 결과를 받지 못한 8건을 읽기 전용 SQL로 가를 때까지 3절 6단계(GPT 다시 적용)를 미룬다(제안).

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
   `failed:JOB_NOT_FOUND`나 `failed:RESULT_HIDDEN_BY_EDGE`로 끝난 문항이 있으면 6번 전에 [4절](#결과를-받지-못한-8건)대로 다시 보낼지부터 정한다.
6. **GPT로 다시 적용**. Gemini에서 결과를 받지 못한 문항이 있으면 [4절](#결과를-받지-못한-8건)의 판정 뒤에 한다. 다시 보내야 한다면 Gemini 흐름이 적용돼 있는 동안 보내야 하기 때문이다.
```bash
cd ~/oreumit-agent && sudo env COMPOSE_PROJECT_NAME=oreumit-bootstrap bash deploy/apply-agent-rule-action.sh
```
7. 공유할 집계만 출력해 붙인다. 질의·정답·장소 ID·응답 원문은 들어 있지 않다.
```bash
cd ~/oreumit-agent && cat deploy/gateway-eval-reports/gpt-report.json deploy/gateway-eval-reports/gpt-collection.json deploy/gateway-eval-reports/gemini-report.json deploy/gateway-eval-reports/gemini-collection.json
```
   토큰·호출 수는 [32 문서의 확인 SQL](32-agent-gateway-tunnel.md#지연토큰부하-측정-도구)로 함께 본다(`model` 열로 두 흐름이 나뉜다).

### 결과를 해석하지 못했거나 Cloudflare가 가렸을 때

2026-11-25 AWS 첫 시험 2건이 2~4초 만에 `unknown:RESULT_RESPONSE_INVALID`로 끝났다(사용자 제공 출력). 수집기는 Gateway 응답이 예상 형식과 다르면 다시 보내지 않고 `unknown`으로 적는다. 이제 그 줄에 `diagnostic`(HTTP 상태, 응답 항목 이름, 상태·오류 코드·동작 같은 정해진 값, 장소 수)을 함께 적고 화면에도 보인다. 장소 ID와 질의 원문은 적지 않는다. 원인을 고친 뒤 `--retry-unknown`을 붙이면 `unknown`으로 끝난 문항만 다시 보낸다(이미 한 번 실행됐을 수 있어 유료 호출이 1건씩 더 든다).

```bash
cd ~/oreumit-agent && sudo bash deploy/run-gateway-eval.sh collect gpt --limit 2 --retry-unknown
```

결과 조회(GET)에 Gateway 오류 코드 없는 5xx(502·503·504·52x·530)가 오면 수집기는 그 답을 작업 결과로 보지 않는다. Cloudflare 같은 가장자리의 일시 오류로 보고, 제출은 다시 하지 않은 채 5초 뒤 같은 작업을 다시 조회한다. 그 횟수는 `edgeRetries`에 적는다(PR #43). 일시 오류라면 다음 조회에서 결과가 온다. 그런데 Gateway가 502·504로 저장한 결과는 Cloudflare가 자기 오류로 바꿔 답한다(Cloudflare 문서, [4절](#결과를-받지-못한-8건)). 그러면 결과 보존 300초가 지나 404 `JOB_NOT_FOUND`가 올 때까지 같은 답만 받는다. 2026-11-26 Gemini의 8건이 이렇게 끝난 것으로 본다(추정).

이 브랜치의 수집기는 가장자리 오류 바로 다음에(그 사이 다른 답 없이) 404 `JOB_NOT_FOUND`나 410 `JOB_EXPIRED`가 오면 `failed:RESULT_HIDDEN_BY_EDGE`로 적고, 마지막 가장자리 응답의 HTTP 상태를 `edgeStatus`에 남긴다(구현, AWS 미적용). 가장자리 오류 뒤에 결과가 사라졌다는 기록이며, 그 결과가 무엇이었는지는 이 기록으로 알 수 없다. `failed`이므로 `--retry-unknown`으로도 다시 보내지 않는다. 이미 적힌 기록은 고치지 않는다. 그래서 Gemini의 8건은 `failed:JOB_NOT_FOUND`로 남는다. 채점기는 두 코드 모두 실패(`transport_error`)로 센다.

## 4. Gemini 수집 결과(2026-11-26, 사용자 제공 출력)

사용자가 AWS에서 3절 5단계(Gemini 수집)를 마치고 붙여 준 출력이다. 작성자의 AWS 실행이 아니다. 흐름은 `vertex-8-call`(메시지 저장 끔)이었다. 채점 보고서는 아직 이 문서에 없다. 본 실행(2026-11-25 시험 2건 뒤의 94건)의 마지막 줄은 다음과 같다.

```json
{"status":"completed","stoppedReason":null,"outcomesThisRun":{"accepted":82,"failed:JOB_NOT_FOUND":8,"failed:RECOMMENDATION_BLOCKED":4},"submissionsThisRun":94,"casesRemaining":0}
```

| 결과 | 문항 | 내용 |
|---|---:|---|
| 수락 | 84 | 본 실행 82 + 2026-11-25 `--limit 2` 시험 2. 시험 2건은 [32 문서](32-agent-gateway-tunnel.md#집계-sql-aws-실행-결과2026-11-25-사용자-제공-출력)의 11-25 Gemini 실행 2건(수락 2)과 수가 맞는다 |
| `failed:RECOMMENDATION_BLOCKED` (HTTP 422) | 4 | `CASE-044`·`063`·`092`·`094`. 최종 검증이 막은 정상 차단 답이다. 채점기는 `invalid_output`으로 센다 |
| `failed:JOB_NOT_FOUND` (HTTP 404) | 8 | `CASE-005`·`012`·`014`·`015`·`051`·`053`·`080`·`095`. 결과를 받지 못했다(아래) |
| 합 | 96 | `casesRemaining:0` |

### 결과를 받지 못한 8건

**관측(사용자 제공 출력).** 8건은 문항마다 `edgeRetries` 59~60, `totalSeconds` 361~395였고, 마지막 조회는 404 `JOB_NOT_FOUND`였다. `edgeRetries`는 결과 조회가 Gateway 오류 코드 없는 5xx(502·503·504·52x·530) JSON을 받아 5초 뒤 다시 조회한 횟수다(코드 확인: 수집기 클라이언트는 JSON 응답만 받아들인다). 사용자의 Cloudflare 대시보드에는 2026-11-26 11:17:42~11:18:17 KST에 한 작업의 `GET agent.oreumit.com /v1/jobs/gw-…`가 502 Bad Gateway로 기록돼 있었고, 그 뒤로는 202·200 기록이 보였다. 사용자가 cloudflared 컨테이너 로그를 찾아봤을 때 원점에 닿지 못했다는 오류 줄은 없었다.

**원인(추정, 근거가 강함).**

1. 지금 AWS의 Gateway(이 브랜치 이전 코드)는 상류 실패(Langflow·Agent 오류, 형식이 맞지 않는 최종 출력, MCP 호출 실패)를 작업 결과 HTTP 502 `GATEWAY_UNAVAILABLE`로 저장한다(`services/agent-gateway/upstream.mjs`의 `UpstreamError` 기본값, `jobs.mjs`의 `errorResult`). 끝난 결과는 300초 보관하고(`JOB_POLICY.resultTtlMs`), 그 뒤의 조회는 404 `JOB_NOT_FOUND`다.
2. Cloudflare는 원점이 502·504로 답하면 자기 502·504 오류로 바꿔 답한다([Cloudflare 문서](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-502-504/)). 원점의 502·504를 그대로 넘기는 영역 설정 `origin_error_page_pass_thru`는 Enterprise 전용이다. 2026-04-27부터는 요청이 `Accept: application/json`을 보내면 5xx 오류를 JSON(`cloudflare_error`·`retryable` 등)으로 답한다([변경 기록](https://developers.cloudflare.com/changelog/post/2026-04-27-structured-responses-for-5xx-errors/), [오류 응답 설명](https://developers.cloudflare.com/fundamentals/reference/error-responses/)).
3. 수집기(`deploy/collect-gateway-eval.py`)는 `Accept: application/json`을 보낸다. 그래서 Gateway 코드가 없는 올바른 JSON을 받았고, 이를 가장자리 일시 오류로 보아 5초 뒤 같은 결과를 다시 읽었다. 저장된 결과가 사라질 때까지 이를 되풀이했다. 59~60회 × 5초 ≈ 300초가 보관 시간과 맞는다.
4. 터널 장애로는 설명되지 않는다. 8건마다 바로 다음 문항이 곧바로 접수됐고(202), cloudflared 로그에도 원점 연결 오류가 없었다.

**아직 확인하지 않은 것.** 8건의 실행이 실제로 무엇이었는지 모른다. 읽기 전용 [지연·토큰 집계 SQL](32-agent-gateway-tunnel.md#지연토큰-집계-sql)로 가른다. 명령은 그 절의 것을 그대로 쓴다(SQL이 명령 안에 들어 있고, 같은 SQL이 `deploy/agent-run-metrics.sql`에 있다). 모델이나 서비스를 부르지 않고 집계만 나온다. [2]에서 `kst_day` `2026-11-26`, `model` `gemini-3.8-flash` 행을 본다. 본 실행 94건이 모두 이날(KST) 돌았고 다른 Gemini 요청이 없었다면 그 행의 `runs`는 94다. 이 SQL은 웹 요청과 합성 요청을 나누지 못한다. 모델을 부르기 전에 실패한 실행은 `model`이 `-`인 행에 나온다.

| 그 행의 값 | 뜻 | 8건(제안) |
|---|---|---|
| `accepted` 82·`blocked` 4, `agent_error`+`no_final` 8 | 8건의 Langflow 실행이 최종 검증까지 가지 못했다. 모델·Agent 쪽 실패다 | 다시 보내지 않는다 |
| `accepted` 90·`blocked` 4, `agent_error`+`no_final` 0 | Langflow 실행은 흐름 안 최종 검증을 통과했고, 그 뒤 Gateway 단계(MCP 재검증 호출 등)가 실패했다. 인프라 실패다 | Gemini 흐름이 적용돼 있는 동안 다시 보낸다 |
| 그 밖(둘이 섞이거나 `runs`가 94가 아님) | 이 SQL은 세션 ID를 내지 않아 문항별로 나눌 수 없다 | 다시 보내기 전에 따로 정한다 |

**비교 규칙(제안).**

- 모델·Agent 실패라면 8건은 다시 보내지 않고 실패로 센다(채점기는 `transport_error`로 센다). GPT 수집에서도 `failed`로 끝난 문항은 다시 보내지 않았다. Gemini의 실패만 다시 보내면 Gemini에만 시도를 더 주게 된다.
- SQL이 인프라 실패를 보일 때만 다시 보낸다. 그것도 Gemini 흐름이 적용돼 있는 동안에만 한다. 그래서 3절 6단계(GPT 다시 적용, `apply-agent-rule-action.sh`)는 이 판정 뒤로 미룬다.
- 지금 수집기에는 `failed`로 끝난 문항을 다시 보내는 선택지가 없다(`--retry-unknown`은 `unknown`만 다시 보낸다). 다시 보내기로 정하면 그 방법은 그때 정한다.
- GPT 수집의 32번도 같은 일이었을 수 있다. 결과 조회가 Cloudflare 502 JSON(`cloudflare_error`·`retryable`, Gateway 코드 없음)을 받아 `unknown`으로 끝났고, 다시 보냈다(다른 세션 기록, 커밋 `9620068`). 일시적인 가장자리 오류였는지 가려진 Gateway 실패였는지는 남은 기록으로 알 수 없다. 두 수집을 견줄 때 이 1건을 함께 적는다.

**수정(구현, AWS 미적용).** `claude/gateway-edge-hidden-failure`([PR #PR-NUMBER-TBD](https://github.com/OREUM-IT/jeju-tour-data-analysis/pull/PR-NUMBER-TBD))는 Gateway가 상류 실패를 500으로 답하게 했다([32 문서](32-agent-gateway-tunnel.md#대기실패재시작의-의미)). 수집기는 가려진 결과를 `failed:RESULT_HIDDEN_BY_EDGE`로 적는다([3절](#결과를-해석하지-못했거나-cloudflare가-가렸을-때)). 이 비교를 마치는 데 Gateway 적용은 필요 없다.

## 5. 남는 것과 지우는 것

- `deploy/.local/gateway-eval/`(0700, root): 문항·정답·요청·응답 원문·세부 채점. Git에 넣지 않는다. 비교가 끝나면 사용자가 지워도 된다.
- `deploy/gateway-eval-reports/`: 공유 가능한 집계(채점 요약·수집 결과 수·시간). Git에는 이 저장소의 `reports/`로 옮겨 넣는다.
- Langflow DB에는 운영 요청처럼 `gw-` trace가 남는다. 메시지는 저장되지 않는다. 정리는 [32 문서](32-agent-gateway-tunnel.md#langflow-메시지-저장-끄기와-오래된-원문-정리)의 도구를 따른다.

## 6. 도구

| 파일 | 역할 |
|---|---|
| `services/jeju-mcp/scripts/gateway-eval.mjs` | `prepare`(새 96문항·계획), `requests`(Gateway 요청), `reference`(모델 없는 기준선), `score`(가져오기·채점). 네트워크를 쓰지 않는다 |
| `deploy/collect-gateway-eval.py` | 서명 요청으로 Gateway에 1건씩 보내고 결과를 `.local`에 이어 쓴다. `--execute-model` 없이는 아무것도 보내지 않는다 |
| `deploy/run-gateway-eval.sh` | 위 둘을 묶는다. Node 단계는 이미 빌드된 `jeju-mcp` 이미지의 일회용 컨테이너에서 네트워크 없이, 저장소 코드를 읽기 전용으로 붙여 실행한다 |
| 평가 틀 | `gateway` 시스템 추가(`baseline-adapters.mjs`의 `adaptGatewayResponse`). Gateway의 `gw-` ID를 하네스의 실행 ID로 바꾸는 것 말고는 응답을 고치지 않는다 |

확인: `services/jeju-mcp` 테스트 265개(새 5개 포함), deploy 테스트 237개 중 새 6개 포함 236개 통과(나머지 1개는 이 작업 환경의 git 제한으로 main에서도 실패하는 `test_export_catalog`). 컨테이너와 같은 경로 배치(`/app`·`/data`·`/reports`)에서 `prepare`→`requests`→가짜 Gateway 결과→`score`를 끝까지 돌려 확인했다. 실제 Docker·AWS·모델 실행은 하지 않았다.
