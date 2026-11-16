# 학습·시험·오답·복습 (O09)

공고가 요구하는 것에서 시작해 학습 결과까지 하나의 사슬로 잇는다. 학습은 **계획**이고 경험이 아니다.

```text
저장 공고(saved)의 요건 한 줄  ──(사용자가 연결)──▶  주장(claim)   ⇒ 입증함
        │
        ├─▶ 학습 항목(study_items) ─▶ 학습 기록(sessions) · 연습 응시(attempts) · 오답(mistakes)
        └─▶ 시험 준비(study_assessments, 전형 절차) ─▶ 응시(attempts, 점수) ─▶ 오답 ─▶ 복습 일정
                                                                          │
                     (사용자가 "경험 후보로 올리기"를 누를 때만) ─▶ 확인 대기 주장(extracted)
```

## 계획과 경험의 분리

- 학습 진행·완료, 응시 점수, 오답, 복습은 `claims`·`experiences`에 아무것도 쓰지 않는다.
  학습 완료는 "학습함"이고 "입증함"이 아니다 (충족 보기에서 `studied`와 `evidenced`를 구분).
- `POST /core/study/items/{id}/claim-candidates {experience_id, statement, kind, attempt_id?}`만 주장을 만든다.
  - 문장은 사용자가 쓴다. 자동으로 만들지 않는다.
  - 학습 결과가 있어야 한다: 항목이 `done`이거나, 그 항목의 응시 기록(`attempt_id`)을 고른다. 아니면 409 `no_study_result`.
  - 주장은 확인 권한(approve)이 있어도 항상 `extracted`, `contribution = unknown`으로 시작한다. 확정은 Brain 확인 흐름(`claims.confirm`)에서만.
  - 학습 기록은 주장의 근거(`claim_evidence`)로 연결하지 않는다. 출처는 `study_claim_candidates`에만 남는다.
  - 항목이 공고 요건에서 왔으면 그 요건 ↔ 새 주장을 연결한다 → 충족 보기에서 `claim_pending`.

## 공고에서 준비 항목 만들기 (plan §8)

- 확정한 공고(`saved`·`archived`)의 `fields` 값만 쓴다. 초안이면 409 `posting_not_saved`.
- 요건 문장은 그 시점 그대로 `study_requirements`에 복사한다(불변). 공고를 다시 확정해 문장이 빠지면 충족 보기에서 `current: false`로 남는다.
- `POST /core/study/postings/{id}/plan`
  - 본문 없음: 자격·우대 요건 중 확정 주장이 연결되지 않았고 살아 있는 학습 항목도 없는 줄 → 학습 항목. 전형 절차는 단계로 나눠(`서류 - 코딩테스트 - 면접`, `→`, `>`, `·` 등) 시험으로 읽히는 단계(코딩테스트·필기/인적성·과제)만 시험 준비 항목으로. 서류·면접·알 수 없는 단계는 만들지 않는다.
  - `{"lines": [{field, text}]}`: 고른 줄만.
  - 여러 번 눌러도 같은 항목을 다시 만들지 않는다.
- 단계 분류는 키워드 규칙이다(`domain/study.py`). 모르면 `kind: null` — 추측하지 않는다.

## 요건 충족 보기

`GET /core/study/postings/{id}/coverage` — 요건 줄마다 연결된 주장, 학습 항목(최근 응시), 시험(최근 응시), 전형 단계 분류.

| coverage | 뜻 |
|---|---|
| `evidenced` | 확정 주장(`self_confirmed`/`evidence_supported`)이 연결됨 |
| `claim_pending` | 연결된 주장이 확인 대기 |
| `studied` | 학습 항목 완료 또는 시험 완료, 경험 근거 없음 |
| `studying` | 학습 계획·진행 중 |
| `uncovered` | 없음 → 학습 보완 또는 지원 재검토 |

- 판정 근거(`basis: user_links`)는 사용자가 연결한 주장과 학습 기록뿐이다. 공고 문장과 주장 문장을 자동으로 비교해 충족으로 판정하지 않고, 합격 가능성을 나타내지 않는다.
- `counts`는 현재 공고의 자격·우대 요건만 센다 (전형 절차 제외).
- 요건 ↔ 주장 연결: `POST /core/study/postings/{id}/claims {field, text, claim_id}`, 끊기: `DELETE /core/study/requirements/{rid}/claims/{claim_id}`.

## 복습 일정 (SM-2 축약, 결정적)

오답을 적으면 다음 날 첫 복습. 배율(ease)은 천분율 정수, 정수 연산만 쓴다.

| 평가 | 간격 | 배율 |
|---|---|---|
| `again` | 1일, 반복 수 0 | −200 (하한 1300) |
| `hard` | 첫 회 1일, 이후 ×1.2 (최소 +1일) | −150 |
| `good` | 1일 → 3일 → 간격×배율 (최소 +1일) | 그대로 |
| `easy` | 첫 회 3일, 이후 간격×배율×1.3 (최소 +1일) | +150 |

최대 365일. 예: good 네 번 → 1, 3, 7, 17일. `GET /core/study/reviews/due?until=&limit=`는 `until`(기본 지금)까지 밀린 `open` 오답을 오래된 순으로. 다 익힌 오답은 `PATCH /core/study/mistakes/{id} {status: resolved}` → 목록에서 빠지고 복습하면 409.
복습 1회마다 `study_reviews`에 입력·출력(간격·배율·다음 날짜)을 남긴다.

## 경로

| 경로 | scope |
|---|---|
| `GET /core/study/postings/{id}/coverage` | study.read |
| `POST /core/study/postings/{id}/plan`, `POST /core/study/postings/{id}/claims` | study.write |
| `DELETE /core/study/requirements/{rid}/claims/{claim_id}` | study.write |
| `GET/POST /core/study/items`, `GET/PATCH /core/study/items/{id}` | read / write |
| `POST /core/study/items/{id}/sessions` (분·메모·자료 제목/URL) | study.write |
| `POST /core/study/items/{id}/claim-candidates` | study.write |
| `GET/POST /core/study/assessments`, `GET/PATCH /core/study/assessments/{id}` | read / write |
| `POST /core/study/attempts` (결과 passed/failed/unknown, 점수/만점) | study.write |
| `POST /core/study/mistakes`, `PATCH /core/study/mistakes/{id}` | study.write |
| `POST /core/study/mistakes/{id}/reviews {grade}` | study.write |
| `GET /core/study/reviews/due` | study.read |

`study.read` = read, `study.write` = draft-write (편집자도 학습을 기록하고 후보를 올릴 수 있다. 확정은 못 한다).

## 범위 밖

- 문제 은행·자동 채점·코드 실행 채점 (plan §12: 격리 환경 검증 전까지 제공하지 않음). 채용시험 대리 응시 지원 없음.
- 공개되지 않은 시험 내용을 사실로 확정하지 않는다. 오답·메모는 사용자의 기록일 뿐이다.
