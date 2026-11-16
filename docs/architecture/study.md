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
- 합쳐진 학습 항목(`merged`)은 합친 항목으로 바꿔 보여 준다 (최근 응시도 합친 항목 기준).

### 연결 후보 제안 (검색, c0013)

`GET /core/study/postings/{id}/claim-suggestions?field=&text=&limit=` — 요건 줄마다 브레인 검색(키워드, 의미 검색을 설정했으면 함께)으로 주장 후보를 찾는다.
`text`가 없으면 지금 공고의 자격·우대 요건 전부(최대 40줄).

- **아무것도 연결하지 않는다.** 충족 보기도 바뀌지 않는다. 사용자가 후보를 보고 "연결"을 눌러야 위의 연결 API가 불린다.
- 검색어는 요건 문장에서 기술 낱말·연차 같은 핵심어를 뽑아 만든다 (`requirement_query`). 낱말이 없으면 문장 그대로.
- 이미 연결된 주장, 이의 제기(`disputed`)·폐기 주장은 뺀다. 확인 대기 주장은 상태를 붙여 보여 준다.
- 응답 `basis: search_suggestion`, `mode: keyword|hybrid`. 검색 점수는 "닮았다"는 뜻일 뿐 판정이 아니다. 판정 모델(게이트웨이 뒤)은 붙이지 않았다.
- 웹: 충족 화면의 요건 줄마다 "후보 찾기" → 후보별 "연결".

### 빠진 요건 정리

`POST /core/study/postings/{id}/requirements/cleanup {requirement_ids?, drop_items?}` — 다시 확정하면서 공고에서 빠진 요건(`current: false`)을 지운다.

- 지금 공고에 있는 요건을 고르면 409 `requirement_current`.
- 요건 ↔ 주장 연결은 함께 지워진다. 학습 항목·시험은 남고 요건 연결만 끊긴다(`ON DELETE SET NULL`).
  `drop_items: true`면 끊기는 학습 항목(계획·진행 중)은 `dropped`, 예정 시험은 `cancelled`로.
- 웹: 충족 화면에 빠진 요건이 있으면 "빠진 요건 정리" (학습 항목은 그대로).

## 저장 공고 요건 빈도 (plan §8, c0013)

`GET /core/study/requirements/frequency?include_archived=&min_postings=&limit=` — "저장 공고 N개 중 M개가 X 요구".

- 센 대상은 **내가 확정해 저장한 공고**(`saved`, 선택하면 `archived`도)의 자격·우대 요건뿐이다. 응답 `basis: saved_postings_only`. 채용 시장 통계가 아니다.
- 묶기는 규칙이다 (`domain/study.py` `requirement_terms`): 영문 기술 낱말은 소문자·별칭(`k8s`→kubernetes, `postgres`→postgresql, `js`→javascript), 한글 기술명은 영문 키로(`도커`→docker, `쿠버네티스`→kubernetes), 연차(`3년 이상`→"경력 3년 이상"), 짧은 한글 기술·요건 낱말 목록(`자료구조`, `협업`, `영어` 등). 불용어는 뺀다. 형태소 분석은 없다.
- 한 공고가 같은 낱말을 여러 줄에 써도 1개로 센다. 그룹마다 자격/우대 줄 수와 예시 문장(최대 3개).
- 웹: 학습 화면 "요건 빈도" 탭.

## 학습 항목 합치기·지우기 (c0013)

- `POST /core/study/items/{id}/merge {into}` — `id`를 `into`에 합친다.
  - 학습 기록(sessions)·오답(mistakes)은 `into`로 옮긴다 (고칠 수 있는 행).
  - 응시(attempts)·경험 후보는 지우거나 고치지 않는 기록이라 원래 항목에 그대로 두고, 원래 항목을 `merged`로 표시한다. 합친 관계는 `study_item_merges`(원래 항목당 1행)에 남는다.
  - 합친 항목 화면(`merged_from`)과 충족 보기에서 원래 항목의 응시·후보가 함께 보인다. 원래 항목에 이미 합쳐져 있던 항목도 `into`로 다시 가리킨다.
  - `into`에 요건·시험·메모가 없으면 원래 항목의 것을 물려받는다.
  - `merged` 항목은 목록에서 숨기고(`status=merged`로는 조회), 상태를 바꾸거나 기록을 더할 수 없다 (409 `item_merged`). `merged`로 직접 바꿀 수도 없다.
- `DELETE /core/study/items/{id}` — 항목과 학습 기록·오답(이 항목으로 합쳐진 항목 포함)을 지운다.
  응시·경험 후보·복습 기록이 있으면 409 `item_has_records` — `dropped`로 두거나 다른 항목에 합친다.

## 복습 일정 (SM-2 축약, 결정적)

오답을 적으면 다음 날 첫 복습. 배율(ease)은 천분율 정수, 정수 연산만 쓴다.

| 평가 | 간격 | 배율 |
|---|---|---|
| `again` | 1일, 반복 수 0 | −200 (하한 1300) |
| `hard` | 첫 회 1일, 이후 ×1.2 (최소 +1일) | −150 |
| `good` | 1일 → 3일 → 간격×배율 (최소 +1일) | 그대로 |
| `easy` | 첫 회 3일, 이후 간격×배율×1.3 (최소 +1일) | +150 |

최대 365일. 예: good 네 번 → 1, 3, 7, 17일. 다 익힌 오답은 `PATCH /core/study/mistakes/{id} {status: resolved}` → 목록에서 빠지고 복습하면 409.
복습 1회마다 `study_reviews`에 입력·출력(간격·배율·다음 날짜)을 남긴다.

### 마감 기준: 표시 시간대의 하루 끝 (c0013)

`GET /core/study/reviews/due?tz=&until=&limit=` — 오늘(표시 시간대 날짜)이 끝나기 전에 예정된 `open` 오답을 오래된 순으로.

- 시간대: `tz` 파라미터 → 공간 설정(`study_settings.timezone`) → 기본 `Asia/Seoul`. IANA 이름만 (`invalid_timezone` 400).
- 마감 = 그 시간대의 다음 자정(UTC로 바꿔 비교). 예: 서울 23:30에 내일 08:00(서울) 예정 오답은 아직 아니다, 오늘 22:00 예정은 포함.
- `until`을 주면 그 시각까지(포함)로 덮어쓴다 (이전 동작).
- 설정: `GET/PUT /core/study/settings {timezone}` → `{timezone, is_default, today}`. 웹 복습 탭에서 고른다 (브라우저 시간대 후보 표시).

### 시험 D-day

`GET /core/study/assessments/upcoming?days=30&tz=&limit=` — 일정(`scheduled_at`)이 있는 예정 시험 중 오늘부터 `days`일 안의 것, 가까운 순. `d_day`는 표시 시간대 날짜 차이(당일 0). 오늘 화면 "오늘 복습" 위에 3개까지 `D-n`으로 보인다.

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
| `GET /core/study/mistakes?status=open\|resolved` (다음 복습순) | study.read |
| `POST /core/study/mistakes`, `PATCH /core/study/mistakes/{id}` | study.write |
| `POST /core/study/mistakes/{id}/reviews {grade}` | study.write |
| `GET /core/study/reviews/due` (`tz`, `until`) | study.read |
| `GET /core/study/settings` / `PUT /core/study/settings` | read / write |
| `GET /core/study/assessments/upcoming` | study.read |
| `GET /core/study/postings/{id}/claim-suggestions` | study.read |
| `POST /core/study/postings/{id}/requirements/cleanup` | study.write |
| `GET /core/study/requirements/frequency` | study.read |
| `POST /core/study/items/{id}/merge`, `DELETE /core/study/items/{id}` | study.write |

`study.read` = read, `study.write` = draft-write (편집자도 학습을 기록하고 후보를 올릴 수 있다. 확정은 못 한다).

## 범위 밖

- 문제 은행·자동 채점·코드 실행 채점 (plan §12: 격리 환경 검증 전까지 제공하지 않음). 채용시험 대리 응시 지원 없음.
- 공개되지 않은 시험 내용을 사실로 확정하지 않는다. 오답·메모는 사용자의 기록일 뿐이다.
