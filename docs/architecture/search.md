# 검색 (O05)

브레인 검색은 PostgreSQL 안에서 끝난다. 키워드 검색은 항상 켜져 있고, 의미 검색(임베딩)은 설정했을 때만 더한다.

## 구조

```text
질문 ─┬─ 키워드: pg_trgm word_similarity + 낱말 부분 일치(ILIKE) ── 원본 테이블을 그대로 (항상 최신)
      └─ 의미: 질문 임베딩 → pgvector 코사인 거리(정확 검색) ── 본문 해시가 같은 벡터만
            ↓
       RRF(k=60)로 합침. 점수가 같으면 원문 낱말이 실제로 들어 있는 결과가 앞.
```

- 대상: 폐기되지 않은 주장(`claim`), 원본별 최신 파싱 블록(`block`), 인터뷰 답(`answer`).
- 학습 기록(c0013): 학습 메모(`study_note`, 메모가 있는 학습 기록)와 오답(`study_mistake`, 문제·내 답·정답·이유).
  - **기본 검색에는 들어가지 않는다.** `kinds=study_note,study_mistake`로 고를 때만. MCP `search_brain`·문서 근거 검색은 그대로 주장·블록·답만 보므로 학습 기록이 경험 근거로 섞이지 않는다.
  - 결과마다 `record`가 붙는다: `experience`(주장·블록·답) 또는 `study`(학습 기록). 학습 결과는 `study_item_id`·`study_title`도. 웹 검색은 모든 종류를 묻고 학습 결과에 "학습 기록 · 경험 아님"을 붙인다.
  - 같은 공간·등급 규칙: 학습 메모는 `private` 등급으로 다룬다 (외부 임베딩은 엔드포인트가 `private`을 허용할 때만).
- 공간 격리: RLS. 검색 SQL은 세션의 공간만 본다. 시험: 같은 자료를 두 공간에 넣고 30문항 모두에서 다른 공간 결과 0건.
- 한국어: 조사가 붙은 원문(`시간을`)에도 낱말(`시간`) 부분 일치로 걸린다. DB 로캘이 한글을 글자로 분류해야 trigram이 동작한다 (`en_US.utf8`·`C.UTF-8` 확인, `C`는 불가).
- HNSW 등 근사 색인은 두지 않았다. 개인 자료 규모에서는 정확 검색이 충분하고, 재현율 손실이 없다.

## 임베딩: API 또는 로컬

`models.toml`의 `routes.embed` 하나로 고른다. 없으면 키워드 검색만 (가장 가볍다).

| 방식 | 설정 | 비용·자원 | 자료 등급 |
|---|---|---|---|
| OpenAI API | `embed = "openai/text-embedding-3-small"` (+ `[embedding] dimensions`) | $0.02 / 1M 토큰, 로컬 메모리 거의 없음 | 엔드포인트 `data_classes`가 허용한 등급만 (restricted는 항상 제외) |
| 로컬 (fastembed, ONNX/numpy, torch 없음) | `preset = "local"` 엔드포인트 + `embed = "<그 이름>/<모델>"` | 무료, 모델 파일 + 사용 중 RAM (아래) | 모든 등급 (기계 밖으로 안 나감) |

- 임베딩도 대화 호출과 같은 게이트웨이를 지난다: egress → 단가 → 월 예산 → 호출 → 장부(`purpose = 'embed'`).
- 검색어 임베딩도 외부 전송이다. API 임베딩이면 `egress` 권한이 있는 사람만 의미 검색을 쓰고, 나머지는 키워드 결과에 `semantic_note = egress_scope_required`.
- 벡터는 `(대상, 공간)`마다 하나. 공간 = `제공자:모델:차원설정`. 다른 공간끼리는 섞지 않는다. 설정을 바꾸면 `personal-api reindex`.
- 색인은 worker 작업(`kind = 'embed'`, 공간당 대기 1개)이다. 파싱 완료·추출·주장 추가/수정·인터뷰 답·학습 메모·오답에서 예약된다. 본문 해시가 같으면 다시 만들지 않는다.
- 실행 중 바뀐 항목 (c0013): 예약은 공간별 advisory lock을 **공유**로, 색인 작업의 "남은 대상 확인 → 작업 끝냄"은 같은 lock을 **배타**로 잡는다. 남은 대상이 없을 때만 같은 트랜잭션에서 작업을 `done`으로 바꾸므로, 실행 중에 들어온 예약은 (1) 아직 commit 전이면 확인이 끝날 때까지 기다렸다가 끝난 작업 뒤에 새 작업을 넣고, (2) 이미 commit했으면 확인에서 바뀐 대상이 보여 한 바퀴 더 돈다. 대상이 남으면 핸들러는 `retry_after=0`으로 곧바로 다시 돈다. 시험: 임베딩 도중 주장을 고쳐도 마지막 벡터가 새 본문 해시.
- 로컬 모델은 처음 쓸 때 올리고 `CAREER_EMBED_IDLE_SECONDS`(기본 300초) 동안 안 쓰면 내린다.

## 측정 (합성 30문항, `python -m career_core.evals.search_eval --models <파일>`)

24문항은 원문 낱말이 들어간 질문, 6문항은 다른 표현(`온콜 경험` → "장애 대응 당번")이다. 임시 DB에서 돌고 사용자 DB는 건드리지 않는다.
2026-11-17, Apple Silicon, `CAREER_EMBED_THREADS=2`, fastembed 0.9.1. RSS는 모델을 올려 30문항 색인·검색하는 동안의 최대값에서 import 직후 값을 뺀 것이다.

| 구성 | 전체 R@1 | 전체 R@5 | 다른 표현 R@5 | 모델 파일 | 사용 중 최대 RSS(모델 단독) |
|---|---|---|---|---|---|
| 키워드만 | 25~26/30 | 28/30 | 4/6 | – | – |
| + potion-multilingual-128M (256d, 정적), fastembed 기본 로딩 (이전 기본) | 27/30 | 29/30 | 5/6 | 0.51GB | ~1.44GB |
| + potion-multilingual-128M, `mmap=True` (**지금 기본**) | 같은 벡터 | 29/30 | 5/6 | 0.51GB | ~0.95GB |
| + potion-multilingual-128M, 자체 로더(mmap, 실험) | 26/30* | 29/30 | 5/6 | 0.51GB | ~0.73GB |
| + potion-multilingual-128M, 자체 로더 int8 양자화(실험) | 27/30 | 29/30 | 5/6 | 0.51GB | ~0.85GB |
| + potion-base-8M (영어 전용, 256d) | 25/30 | 29/30 | 5/6 | 0.03GB | ~0.04GB |
| + potion-retrieval-32M (영어 전용, 512d) | 26/30 | 29/30 | 5/6 | 0.13GB | ~0.16GB |
| + paraphrase-multilingual-MiniLM-L12-v2 (384d) | 24/30 | 28/30 | 4/6 | 0.22GB | ~0.70GB |
| + embeddinggemma-300m (768d) | 27/30 | 29/30 | 5/6 | 1.24GB | ~0.79GB |
| + Qwen3-Embedding-0.6B 양자화 (1024d) | 27/30 | 29/30 | 5/6 | 1.12GB | ~2.5GB |
| + multilingual-e5-large (1024d, 2026-11-16 측정) | 28/30 | 29/30 | 5/6 | 2.2GB | ~3.7GB(전체 프로세스) |

\* 벡터는 `mmap=True`와 같다. R@1 ±1은 점수가 같은 결과의 순서(무작위 UUID) 차이다.

- potion 메모리의 내역: float32 임베딩 행렬 ~0.49GB, Rust Unigram 토크나이저 상주 ~0.73GB(줄일 수 없는 바닥), fastembed가 18MB `tokenizer.json`을 `json.load`할 때 순간 ~0.22GB. `mmap=True`는 행렬을 파일에서 바로 읽어 행렬 사본과 일부 순간 메모리를 없앤다. 벡터·재현율은 그대로다. (이전 표의 ~1.6GB는 import 메모리까지 센 값이다.)
- potion-base-8M·retrieval-32M은 메모리가 아주 작지만 영어 WordPiece라 한글을 자모로 쪼갠다. 이 평가에서 점수가 비슷한 것은 질문 대부분이 원문 낱말을 포함해 키워드 검색이 끌어올리기 때문이라 기본값으로 두지 않는다.
- embeddinggemma-300m은 다른 표현 재현율이 같고 RSS가 약간 작지만 모델 파일이 2.4배이고 트랜스포머라 정적 모델보다 계산이 무겁다. 뚜렷이 낫지 않아 기본값을 바꾸지 않았다.
- 결론: 기본값은 키워드만. 가벼운 서버에서 의미 검색이 필요하면 OpenAI `text-embedding-3-small`, 오프라인이면 `potion-multilingual-128M`(이제 `mmap=True`로 ~1GB).
- OpenAI 임베딩 재현율은 키가 없어 아직 측정하지 않았다 (어댑터·비용 기록은 모의 서버로 시험).
- 표본이 작다. 실제 자료로 늘린 평가 세트는 사용자 공간 밖(임시 DB)에서만 돌린다.
