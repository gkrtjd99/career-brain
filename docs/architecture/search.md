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
- 색인은 worker 작업(`kind = 'embed'`, 공간당 대기 1개)이다. 파싱 완료·추출·주장 추가/수정·인터뷰 답에서 예약된다. 본문 해시가 같으면 다시 만들지 않는다.
- 로컬 모델은 처음 쓸 때 올리고 `CAREER_EMBED_IDLE_SECONDS`(기본 300초) 동안 안 쓰면 내린다.

## 측정 (합성 30문항, `python -m career_core.evals.search_eval --models <파일>`)

24문항은 원문 낱말이 들어간 질문, 6문항은 다른 표현(`온콜 경험` → "장애 대응 당번")이다. 임시 DB에서 돌고 사용자 DB는 건드리지 않는다.
2026-11-16, Apple Silicon, `CAREER_EMBED_THREADS=2`.

| 구성 | 전체 R@1 | 전체 R@5 | 다른 표현 R@5 | 모델 파일 | 사용 중 최대 RSS(모델 단독) |
|---|---|---|---|---|---|
| 키워드만 | 26/30 | 28/30 | 4/6 | – | – |
| + paraphrase-multilingual-MiniLM-L12-v2 (384d) | 24/30 | 28/30 | 4/6 | 0.22GB | ~0.9GB |
| + potion-multilingual-128M (256d, 정적) | 27/30 | 29/30 | 5/6 | 0.5GB | ~1.6GB |
| + multilingual-e5-large (1024d) | 28/30 | 29/30 | 5/6 | 2.2GB | ~3.7GB(전체 프로세스) |

- 결론: 기본값은 키워드만. 가벼운 서버에서 의미 검색이 필요하면 OpenAI `text-embedding-3-small`, 오프라인이면 `potion-multilingual-128M`.
- OpenAI 임베딩 재현율은 키가 없어 아직 측정하지 않았다 (어댑터·비용 기록은 모의 서버로 시험).
- 표본이 작다. 실제 자료로 늘린 평가 세트는 사용자 공간 밖(임시 DB)에서만 돌린다.
