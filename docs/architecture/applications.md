# 지원·문서·수정안·제출본 (O07)

plan §11(문서 수정·확정·제출본, AI slop 방지, 요건 가시성)의 첫 구현. 테이블: `database.md` "지원·문서 (c0007)".
코드: `domain/documents.py`(순수 규칙), `usecases/applications.py`, `interfaces/http/applications.py`.

## 흐름

```text
저장한 공고(saved) ─▶ 지원(draft) ─▶ 문항마다 문서 + 이력서·자소서 등 추가 문서
문서 저장(base_revision, operations) ─▶ 새 revision (불변)   ← 최신이 아니면 409
문단 ↔ 브레인 주장(확인된 것만 근거로 셈)
검사(validate) ─▶ 글자·바이트 수, 품질 경고, 요건 가시성      ← 코드 계산, 모델 없음
AI 수정안(선택) ─▶ 코드가 사실 변경·조작 문구 거부 ─▶ 사용자가 골라 적용 = 새 revision
제출(approve) ─▶ 문서 본문·근거 문장·공고 항목을 복사한 불변 스냅샷, 지원은 submitted
결과 기록 ─▶ passed / rejected / withdrawn (원인을 단정하지 않는다)
```

## 경로 (`/api/core` 아래)

| 메서드·경로 | 권한(행동 → scope) | 내용 |
|---|---|---|
| `POST /applications {posting_id, title?}` | `applications.write` → draft-write | 저장한 공고만. 문항마다 문서 생성 (201) |
| `GET /applications` · `GET /applications/{id}` | `applications.read` → read | 목록(문항 수 `questions`, 아직 안 쓴 문항 `unwritten`)·상세(문서 요약, 제출본 ID) |
| `POST /applications/{id}/status {status, note?}` | `applications.write` | passed·rejected·withdrawn만. `submitted`는 제출로만 |
| `POST /applications/{id}/documents` | `applications.write` | 문서 추가 (`kind`, `title`, 제한·세는 기준) |
| `GET/PATCH /applications/{id}/documents/{doc}` | read / write | 최신(또는 `?revision=N`) 본문·블록·근거·글자 수. PATCH는 설정만 |
| `GET/POST .../documents/{doc}/revisions` | read / write | 목록 / 저장 (201, 충돌 409) |
| `POST .../documents/{doc}/count {blocks}` | read | 저장 전 글자/바이트 수. 저장과 같은 정규화(NFC, 앞뒤 공백, 빈 문단 제외, `\n` 연결). 60,000자 넘으면 422 |
| `POST .../documents/{doc}/validate {revision?, extra_cliches?}` | read | 검사 결과 (artifact_validate) |
| `POST .../documents/{doc}/suggestions {base_revision, block_ids?, instruction?}` | `documents.ai` → egress | AI 수정안. 모델 미설정 503 `models_not_configured` |
| `GET .../documents/{doc}/suggestions` | read | 수정안 목록(거부 사유 포함) |
| `POST .../suggestions/{sid}/apply {base_revision}` · `/dismiss` | write | 적용(새 revision) / 닫기 |
| `GET /applications/{id}/checks` | read | 문서별 경고 요약 + 모든 문서를 합친 요건 가시성 |
| `POST /applications/{id}/submit {revisions: {doc_id: n}, note?}` | `applications.submit` → approve | 제출본 고정 (201, 확인한 revision이 최신과 다르면 409) |
| `GET /applications/{id}/submission` | read | 제출본 |

오류: 규칙 위반은 422 + 코드(`unknown_block`, `unknown_claim`, `empty_block`, `invalid_count_rule`…), 잠김·상태 충돌은 409
(`application_locked`, `already_submitted`, `posting_not_saved`, `suggestion_stale`, `suggestion_not_open`, `invalid_transition`).
revision 충돌은 `409 {"detail": {"code": "revision_conflict", "current_revision": n, "stale": [...]}}`.

## 문서 본문과 저장

- 본문은 문단(블록) 목록이 원장이다: `{id, text, origin: user|ai, claim_ids}`. 블록 ID는 서버가 만들고 바뀌지 않는다.
  제출 문자열은 블록을 줄바꿈 하나로 잇는다. Markdown·DOCX 같은 출력은 아직 없다.
- 저장 요청: `base_revision`, `operations`, `reason?`, `idempotency_key?` (plan §11의 요청 모양. `evidence_ids`는 `link` 연산의 `claim_ids`).
  - `replace {block_id, text, claim_ids?}` · `insert {text, index?, claim_ids?}` · `delete {block_id}` · `link {block_id, claim_ids}` ·
    `set_blocks {blocks: [{id?, text, claim_ids?}]}`(편집기가 통째로 보낼 때. 같은 ID·같은 글이면 origin 유지)
  - `base_revision ≠ current_revision` → 409. 자동 병합·덮어쓰기 없음. 같은 `idempotency_key` 재전송은 처음 결과를 돌려준다. 내용이 같으면 새 revision을 만들지 않는다.
  - 본문은 NFC로 정리하고 줄바꿈은 `\n`으로 통일한다. 보이지 않는 문자는 지우지 않는다(검사가 경고해야 한다).
- 제출한 지원의 문서는 더 고치지 않는다(`application_locked`). 다음 지원은 새 지원으로 만든다.

## 글자·바이트 수 (§17.20)

`count_text(text, CountRule, char_limit, byte_limit)` — 모델이 센 값은 쓰지 않는다(수정안 응답에 숫자가 있어도 무시).

| 설정 | 값 | 의미 |
|---|---|---|
| `spaces` | include / exclude | 제한과 비교하는 `chars`가 공백 포함인지. 공백 제외는 줄바꿈도 뺀다 |
| `newlines` | one / two / none | 줄바꿈을 1글자(`\n`)·2글자(`\r\n`로 보내는 양식)·0글자로 |
| `encoding` | utf-8 / cp949 / euc-kr | 바이트 수 기준. 바이트는 공백 포함. 인코딩에 없는 문자는 `unencodable`로 따로 알린다 |

문항을 만들 때 문구에서 기준을 읽는다(`rule_from_prompt`): "공백 제외" → exclude, "1000byte" → 바이트 제한, "한글 2byte" → cp949, "(700자)" → 글자 제한.
틀리면 사용자가 `PATCH`로 고친다.

## 품질 검사 (validate)

모두 코드(정규식·단어 일치)다. 같은 입력이면 같은 결과이고, 모델 없이 시험한다. 결과는 경고 목록이지 판정이 아니다.

| 코드 | 언제 | 회귀 시험 |
|---|---|---|
| `generic_company_swap` | 회사명·직무명(또는 "귀사·회사")을 말하는데, 그걸 빼면 숫자·영문 고유명사·공고 고유 단어·연결 근거의 단어가 하나도 없는 문장 | §17.16 |
| `other_company_name` | 공간의 다른 공고 회사명이 남은 문장 (본인 경력·주장에 나오는 조직명은 뺀다) | §17.16 |
| `cliche` (info) | 상투어 목록. 요청마다 `extra_cliches`로 더할 수 있다 | |
| `paragraph_not_specific` (info) | 근거도 없고 구체적인 단어도 없는 문단 | |
| `unsupported_statement` | 경험(행동 + 과거형)·숫자를 말하는데 문단에 **확인된** 주장 연결이 없음 | §17.17 |
| `number_not_in_evidence` | 근거는 있지만 근거·경험 정보에 없는 숫자 | §17.17 |
| `role_not_in_evidence` | 근거에 없는 주도·총괄·리드 같은 역할 표현 | §17.17 |
| `evidence_unconfirmed` | 연결한 주장이 아직 `extracted` | §17.11 |
| `hidden_characters` · `hidden_text_markup` | 폭 없는 공백·방향 제어·태그 문자 / 흰 글씨·크기 0·display:none 같은 서식 | §17.19 |
| `screening_instruction` | 심사 프로그램·모델에게 지시하는 문장 (영어·한국어 패턴) | §17.19 |
| `keyword_repetition` · `keyword_list_unsupported` | 같은 단어를 채워 넣음 / 브레인 근거 없는 공고 키워드 나열 | §17.19 |
| `over_char_limit` · `over_byte_limit` (error) · `unencodable_characters` | 문서 설정 기준 | §17.20 |

### 요건 가시성 (§17.18)

공고 `requirements`·`preferred` 문장마다 판정 단어를 뽑는다: 영문 기술명이 있으면 그중 하나("Python 또는 Go"), 없으면 한국어 명사 두 개 이상.
영문은 단어 경계로 맞춘다("PostgreSQL" 안의 "SQL"은 일치가 아니다). 브레인 근거 = 공간의 **확인된** 주장(+ 경험 정보).

| 브레인 근거 | 문서에 보임 | `verdict` |
|---|---|---|
| 있음 | 보임 | `met_visible` (+ 그 문단이 근거에 연결됐는지 `linked`) |
| 있음 | 안 보임 | `missing` — 해당 경험을 드러낸다 |
| 없음 | 주장함 | `overclaim_risk` — 근거 추가 또는 문장 삭제 |
| 없음 | 없음 | `unmet` — 보완 학습·지원 재검토 |

문서 검사는 그 문서만, `GET /applications/{id}/checks`는 지원의 모든 문서를 합쳐 본다. 값은 "근거가 보이는가"이지 합격 가능성이 아니다.
plan이 말한 교체 가능한 판정 모델(게이트웨이 뒤)은 아직 붙이지 않았다 — 단어 일치가 기준선이다.

## AI 수정안 (§17.11, §17.17, §17.19)

1. `documents.ai`(egress) 권한, 용도 `doc_suggest`, 자료 등급 `private`로 게이트웨이를 지난다(전송 정책·단가·예산·장부).
   보내는 것: 대상 문단, 그 문단에 연결된 확인된 주장 문장, 공고 회사·직무·요건, 문항, 사용자 요청(500자). 프롬프트는 "사실을 더하지 말고, 모르면 질문하라"이다.
2. 모델 출력은 `{suggestions: [{block_id, text, reason}], questions}`. 없는 문단·같은 글·중복은 버린다(`dropped`). `questions`는 사용자에게 그대로 보여준다.
3. 코드 검토(`review_suggestion`) — 원문 + 연결 근거와 비교해 다음이 있으면 `rejected`(사유와 함께 저장, 적용 불가):
   `number`(새 숫자), `role`(새 주도·총괄 표현), `organization`(새 조직명), `new_term`(새 영문 고유명사·기술명), `ats_manipulation`(숨김 문자·지시문·키워드 반복).
4. 사용자가 `apply {base_revision}` → 해당 문단만 바꾼 새 revision(`origin = ai_suggestion`, 문단 `origin = ai`). 근거 연결은 그대로.
   문단이 그사이 바뀌었으면 `suggestion_stale`. 문서 보기의 `ai_ratio`가 AI 수정 문단의 글자 비율이다.
5. 수정안은 브레인에 쓰지 않는다. 주장·근거 테이블에 행이 생기지 않고, 수정안 ID를 근거로 걸 수도 없다.

## 제출본 (§17.12)

- `approve` 권한만(`applications.submit`). 요청에 사용자가 확인한 문서별 revision 번호를 넣는다. 하나라도 최신과 다르면 409 `stale` 목록.
- 스냅샷: 문서마다 revision ID·번호·블록·제출 문자열·글자 수·설정, 연결된 주장의 **그때 문장**(상태·기여·경험 정보 포함),
  공고의 그때 `fields`·`confirmed_version_id`·`pending_changes`, 당시 검사 결과. 전체의 `content_hash`.
- `application_submissions`는 SELECT/INSERT만. 이후 주장 문장·공고 항목을 고쳐도 제출본은 바뀌지 않는다(시험으로 확인).
- 확인 대기 중인 공고 변경(`pending_changes`)이 있으면 스냅샷에 같이 남는다.

## plan과 다른 점

- §7의 `artifacts / artifact_versions / artifact_claims` 대신 `application_documents / document_revisions / document_claims`.
  지금 문서는 지원 하나에 속한다. 여러 지원에서 다시 쓰는 포트폴리오 같은 공용 문서는 백로그.
- §7의 `application_stages`(전형·일정)는 만들지 않았다. 상태는 `applications.status` 하나. 전형 단계는 O09(학습·시험 연결)와 함께 본다.
- §11 승인 묶음(행위·대상·내용 해시·만료·멱등 키)은 O08 범위다. 지금 제출은 `approve` scope + 확인한 revision 번호 일치로 막는다.
- 문단 단위 근거 연결(문장 단위는 백로그). 숫자·역할 대조도 그 문단에 연결된 주장 기준이다.

## 한계

- 한국어 처리는 조사 떼기·불용어 정도다. 형태소 분석이 없어 놓치거나 과하게 잡는다. 한글 수 표현("두 배", "천 명")은 숫자로 보지 않는다.
- 역할 대조는 정해진 단어 목록이다("맡아 이끌었다" 같은 표현은 놓칠 수 있다).
- 숨김 서식 검사는 붙여 넣은 HTML/CSS 흔적만 본다. 문서 파일(DOCX·PDF) 내보내기·검사는 아직 없다.
