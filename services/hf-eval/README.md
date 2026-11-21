# hf-eval — Hugging Face 모델 로컬 실험

운영 서비스가 아니라 로컬 실험 도구다. 모델 파일은 처음 실행할 때 Hugging Face에서 내려받아 캐시하고, 그 뒤로는 오프라인으로 동작한다. 카탈로그·평가셋·응답·항목별 점수는 `services/jeju-mcp/.local`에만 두고 Git에는 집계 보고서만 넣는다. 결과와 해석은 [33 문서](../../docs/33-huggingface-models-evaluation.md)에 있다.

| 스크립트 | 하는 일 | 네트워크 |
|---|---|---|
| `scripts/compare-embeddings.mjs` | 임베딩 6종과 BM25(문자 bigram)를 981곳·96문항에서 비교한다. 전체 카탈로그와, 닫힌 템플릿 문법이 허용하는 후보 안에서의 순위를 따로 잰다 | 모델 첫 다운로드만 |
| `scripts/synthtrips-retrieval.mjs` | HF 데이터셋 `ashmib/synthTRIPS`(MIT)의 유럽 200개 도시·질의 2,302개로 같은 임베딩을 외부 검증한다 | 없음(데이터를 미리 받아 둠) |
| `local_llm_server.py` | GGUF 모델을 llama-cpp-python으로 `127.0.0.1`에서만 띄운다. JSON 스키마를 GBNF 문법으로 바꿔 출력을 제한한다. Qwen3.5는 빈 `<think></think>`로 생각 모드를 끈다 | 없음 |
| `scripts/collect-local-llm.mjs` | 템플릿 요청 → 카탈로그 후보 → 임베딩 재정렬 → 조건 판정 → JSON 생성 한 번 → 런타임 최종 검증. 결과는 기존 `evaluate-baselines.mjs import/score`로 채점한다 | 루프백만 |
| `scripts/summarize-local-llm.mjs` | 수집 진단(.local)을 공개 가능한 집계로 줄인다 | 없음 |
| `scripts/embed-texts.mjs` | 앱의 `build-place-embeddings.ts --texts-out` 결과를 로컬 ONNX로 임베딩한다(토큰 없이 앱 벡터 파일 만들기) | 모델 첫 다운로드만 |
| `scripts/compare-app-search.mts` | 앱의 키워드 검색과 임베딩 혼합 후보를 96문항에서 비교한다(Gemini 단계는 실행하지 않음) | 없음 |

## 준비

```bash
npm --prefix services/hf-eval ci --no-audit --no-fund
```

```bash
python3 -m venv ~/hf-venv && ~/hf-venv/bin/pip install "llama-cpp-python[server]==0.3.37"
```

Node 내장 fetch가 프록시를 따라야 하는 환경이면 `NODE_USE_ENV_PROXY=1`을, 로컬 서버에는 `NO_PROXY=127.0.0.1,localhost`를 함께 준다.

## 주의

- `local_llm_server.py`에는 인증이 없다. 루프백 밖으로 열지 않는다.
- 수집기는 계획의 `request`(질의·문맥)와 카탈로그만 읽는다. 평가셋·정답은 비교·채점 스크립트에서 점수 계산에만 쓴다.
- 프롬프트는 평가 결과를 보고 고치지 않는다. 바꾸면 `LOCAL_PROMPT_VERSION`을 올리고 96문항 전체를 다시 수집한다.
