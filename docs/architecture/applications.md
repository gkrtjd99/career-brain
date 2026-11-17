# 지원·문서·수정안·제출본 (O07)

plan §11(문서 수정·확정·제출본, AI slop 방지, 요건 가시성)의 첫 구현. 테이블: `database.md` "지원·문서 (c0007)", "문서 추가 (c0012)".
코드: `domain/documents.py`(순수 규칙), `domain/korean.py`(한국어 수 표현·기술명 별칭), `domain/sensitive.py`(민감 정보),
`domain/document_export.py`(파일 만들기·검사), `usecases/applications.py`, `usecases/documents.py`(공용 문서·설정·내보내기),
`usecases/application_stages.py`, `workflows/documents.py`(일괄 수정안), `interfaces/http/applications.py`.

## 흐름

```text
저장한 공고(saved) ─▶ 지원(draft) ─▶ 문항마다 문서 + 이력서·자소서 등 추가 문서
문서 저장(base_revision, operations) ─▶ 새 revision (불변)   ← 최신이 아니면 409
문단 ↔ 브레인 주장(확인된 것만 근거로 셈)
검사(validate) ─▶ 글자·바이트 수, 품질 경고, 요건 가시성      ← 코드 계산, 모델 없음
AI 수정안(선택) ─▶ 코드가 사실 변경·조작 문구 거부 ─▶ 사용자가 골라 적용 = 새 revision
제출 전 재검사 ─▶ 주민등록번호·남의 연락처·대외비 표시가 있으면 경고 + 확인(sensitive_ack) 필요
제출(approve) ─▶ 문서 본문·근거 문장·공고 항목을 복사한 불변 스냅샷, 지원은 submitted
전형 단계 ─▶ 제출본에 묶인 서류·면접… 일정과 결과 (원인은 적지 않는다)
결과 기록 ─▶ passed / rejected / withdrawn (원인을 단정하지 않는다)

공용 문서(지원 없음) ─복사(출처: 문서·revision)─▶ 지원 문서
```

## 경로 (`/api/core` 아래)

| 메서드·경로 | 권한(행동 → scope) | 내용 |
|---|---|---|
| `POST /applications {posting_id, title?}` | `applications.write` → draft-write | 저장한 공고만. 문항마다 문서 생성 (201) |
| `GET /applications` · `GET /applications/{id}` | `applications.read` → read | 목록(문항 수 `questions`, 아직 안 쓴 문항 `unwritten`, 지금 전형 단계 `stage` = 결과 전인 첫 단계, 다 끝났으면 마지막 단계 `{kind, name, outcome, scheduled_at}`)·상세(문서 요약, 제출본 ID) |
| `POST /applications/{id}/status {status, note?}` | `applications.write` | passed·rejected·withdrawn만. `submitted`는 제출로만 |
| `POST /applications/{id}/documents` | `applications.write` | 문서 추가 (`kind`, `title`, 제한·세는 기준) |
| `GET/PATCH /applications/{id}/documents/{doc}` | read / write | 최신(또는 `?revision=N`) 본문·블록·근거·글자 수. PATCH는 설정만 |
| `GET/POST .../documents/{doc}/revisions` | read / write | 목록 / 저장 (201, 충돌 409) |
| `POST .../documents/{doc}/count {blocks}` | read | 저장 전 글자/바이트 수. 저장과 같은 정규화(NFC, 앞뒤 공백, 빈 문단 제외, `\n` 연결). 60,000자 넘으면 422 |
| `POST .../documents/{doc}/validate {revision?, extra_cliches?}` | read | 검사 결과 (artifact_validate) |
| `POST .../documents/{doc}/suggestions {base_revision, block_ids?, instruction?}` | `documents.ai` → egress | AI 수정안. 모델 미설정 503 `models_not_configured` |
| `GET .../documents/{doc}/suggestions` | read | 수정안 목록(거부 사유 포함) |
| `GET .../documents/{doc}/suggestion-requests?run_id=&limit=` | read | 요청 기록(c0016): 문단 ID·모델 질문·제안/거부/버림 수·실행 ID. 최근 순 (limit 기본 20, 최대 100) |
| `POST .../suggestions/{sid}/apply {base_revision}` · `/dismiss` | write | 적용(새 revision) / 닫기 |
| `GET /applications/{id}/checks` | read | 문서별 경고 요약 + 모든 문서를 합친 요건 가시성 |
| `POST /applications/{id}/submit {revisions: {doc_id: n}, note?}` | `applications.submit` → approve | 제출본 고정 (201, 확인한 revision이 최신과 다르면 409) |
| `GET /applications/{id}/submission` | read | 제출본 |
| `GET /applications/{id}/submission/export/check?format=md\|docx\|html` · `/submission/export?format=` | read | 제출본 파일(문서 내보내기와 같은 writer). 검사만 / 파일. 제출본이 없으면 404 |
| `POST /file-checks/hidden-text` (multipart `file`) | `applications.read` → read | 올린 DOCX·PDF 숨김 글 검사. 저장하지 않는다. 빈 파일 400, 크기 초과 413, 다른 형식 415, 깨진·암호 파일 422 |
| `GET /sources/{source_id}/hidden-text?version_id=` | `sources.read` → read | 이미 올린 원본(파일 저장소가 있을 때) 한 버전의 같은 검사 |
| `POST /applications/{id}/documents/copy {source_document_id, revision?, title?}` | write | 공용 문서의 revision(기본 최신)을 새 지원 문서로 복사 (201) |
| `GET /applications/{id}/sensitive-check` | read | 제출 전 재검사 결과 `{sensitive_hash, requires_ack, findings, summary}` |
| `GET/POST /applications/{id}/stages` · `PATCH/DELETE .../stages/{sid}` | read / write | 전형 단계. 제출본이 있어야 추가 (없으면 409 `not_submitted`) |
| `POST /documents {kind, title, …}` · `GET /documents` | write / read | 공용 문서 만들기·목록(복사해 간 지원 문서 수 `copies`). `kind=question`은 422 `question_not_shared` |
| `/documents/{doc}` 아래 | 지원 문서와 같음 | 보기·설정·revisions·count·validate·suggestions·export. 경로만 다르고 규칙은 같다 |
| `GET .../documents/{doc}/export/check?format=md\|docx\|html&revision=` | read | 파일을 만들어 검사만 (파일 이름·크기·경고) |
| `GET .../documents/{doc}/export?format=…` | read | 파일. 경고 수·코드는 `X-Export-Findings`·`X-Export-Codes` 헤더에도 |
| `GET/PUT /document-settings {cliches?, own_contacts?}` | read / write | 공간의 상투어 목록(`null` = 기본 목록)과 본인 연락처. 준 칸만 바꾼다 |

오류: 규칙 위반은 422 + 코드(`unknown_block`, `unknown_claim`, `empty_block`, `invalid_count_rule`…), 잠김·상태 충돌은 409
(`application_locked`, `already_submitted`, `posting_not_saved`, `suggestion_stale`, `suggestion_not_open`, `invalid_transition`).
revision 충돌은 `409 {"detail": {"code": "revision_conflict", "current_revision": n, "stale": [...]}}`.

## 문서 본문과 저장

- 본문은 문단(블록) 목록이 원장이다: `{id, text, origin: user|ai, claim_ids, spans?}`. 블록 ID는 서버가 만들고 바뀌지 않는다.
  제출 문자열은 블록을 줄바꿈 하나로 잇는다. 파일 출력은 아래 "내보내기".
- 저장 요청: `base_revision`, `operations`, `reason?`, `idempotency_key?` (plan §11의 요청 모양. `evidence_ids`는 `link` 연산의 `claim_ids`).
  - `replace {block_id, text, claim_ids?, spans?}` · `insert {text, index?, claim_ids?, spans?}` · `delete {block_id}` · `link {block_id, claim_ids}` ·
    `link_spans {block_id, spans}` · `set_blocks {blocks: [{id?, text, claim_ids?, spans?}]}`(편집기가 통째로 보낼 때. 같은 ID·같은 글이면 origin 유지)
  - `base_revision ≠ current_revision` → 409. 자동 병합·덮어쓰기 없음. 같은 `idempotency_key` 재전송은 처음 결과를 돌려준다. 내용이 같으면 새 revision을 만들지 않는다.
  - 본문은 NFC로 정리하고 줄바꿈은 `\n`으로 통일한다. 보이지 않는 문자는 지우지 않는다(검사가 경고해야 한다).
- 제출한 지원의 문서는 더 고치지 않는다(`application_locked`). 다음 지원은 새 지원으로 만든다.

### 문장 단위 근거 (spans)

- `spans: [{start, end, claim_ids}]` — 문단 글 안의 위치(코드 포인트, 끝 미포함). 문단마다 50개까지. 위치가 글 밖이거나 주장이 없으면 422 `invalid_spans`.
- span의 주장은 문단 `claim_ids`에도 합친다. 그래서 문단 단위로만 보는 코드(요건 가시성, 제출본 근거, `document_claims`)는 그대로 동작한다.
- 글이 바뀌었는데 spans를 다시 주지 않으면, 옛 span이 가리키던 글을 새 글에서 다시 찾는다. 못 찾으면 그 span만 뺀다(근거 없는 위치를 남기지 않는다).
- 호환: spans가 없는 문단은 예전과 똑같이 저장·해시된다(`blocks_hash`는 spans가 있을 때만 그것을 넣는다). 이전 revision의 해시는 바뀌지 않는다.
- 웹 편집기: 문단에서 글을 고르면 "고른 문장 근거" 버튼이 생긴다. 편집 중에는 위치 대신 인용문을 들고 있다가 저장할 때 위치를 다시 찾는다.

### 공용 문서와 복사

- 공용 문서는 `application_id IS NULL`인 문서다. 포트폴리오·기본 이력서처럼 지원 여러 곳에서 쓰는 글. revision·base_revision/409·멱등 키·근거 규칙·AI 수정안은 지원 문서와 같다.
  공고 문맥이 없으므로 검사에서 "다른 회사 이름"은 공간의 모든 공고 회사명을 대상으로 보고, 요건 가시성은 비어 있다.
- 공고 문항(`question`)은 공용 문서가 될 수 없다(DB `ck_application_documents_shared_kind`도 막는다).
- 복사(`documents/copy`)는 원본 한 revision의 블록·근거 연결·문단 origin을 새 지원 문서의 revision 1로 넣는다. reason은 "공용 문서 '…' r{n}에서 복사".
  복사본은 `source_document_id`·`source_revision_id`를 남기고(`source` 칸: 원본 제목·복사한 revision 번호·원본의 지금 revision), 이후 원본과 서로 영향이 없다.
  근거 연결은 지금도 공간에 있는 주장만 옮긴다. AI 수정 문단은 복사해도 AI 문단이다(AI 비율이 사라지지 않는다).

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
| `cliche` (info) | 공간 설정의 상투어 목록(`document_settings.cliches`, 비어 있으면 기본 목록). 요청의 `extra_cliches`는 계속 받고 그 목록에 더한다 | |
| `paragraph_not_specific` (info) | 근거도 없고 구체적인 단어도 없는 문단 | |
| `unsupported_statement` | 경험(행동 + 과거형)·숫자를 말하는데 **확인된** 주장 연결이 없음. 문단에 spans가 있으면 문장마다(그 문장과 겹치는 span의 주장 + 문단 전체 근거) 보고, 없으면 문단 단위. `detail.evidence_level` = sentence/paragraph | §17.17 |
| `number_not_in_evidence` | 근거는 있지만 근거·경험 정보에 없는 숫자 | §17.17 |
| `role_not_in_evidence` | 근거에 없는 주도·총괄·리드 같은 역할 표현 | §17.17 |
| `evidence_unconfirmed` | 연결한 주장이 아직 `extracted` | §17.11 |
| `hidden_characters` · `hidden_text_markup` | 폭 없는 공백·방향 제어·태그 문자 / 흰 글씨·크기 0·display:none 같은 서식 | §17.19 |
| `screening_instruction` | 심사 프로그램·모델에게 지시하는 문장 (영어·한국어 패턴) | §17.19 |
| `keyword_repetition` · `keyword_list_unsupported` | 같은 단어를 채워 넣음 / 브레인 근거 없는 공고 키워드 나열 | §17.19 |
| `over_char_limit` · `over_byte_limit` (error) · `unencodable_characters` | 문서 설정 기준 | §17.20 |

### 한국어 표현 (규칙 기반, 형태소 분석은 선택)

기본은 형태소 분석기 없이 정규식·사전으로 한다(의존성 0, 같은 입력 같은 결과). `domain/korean.py`.
선택 extra `korean`(kiwipiepy)을 깔고 `CAREER_KOREAN_ANALYZER=kiwi`를 주면 요건 가시성·사실 바뀜의 한국어 낱말 일치가 형태소 단위가 된다:
어미(`최적화했고`)·복합명사 띄어쓰기(`결제시스템` = `결제 시스템`)를 맞추고, 낱말 안의 부분 일치(`영업일` ↔ `영업`, `인증샷` ↔ `인증`)를 일치로 보지 않는다.
사실 바뀜의 "이끔/혼자" 표현도 형태소로 본다. 합성 평가: 요건/문서 쌍 25/40 → 38/40, 사실 바뀜 10/14 → 12/14, 상주 메모리 ~0.42GB (`search.md` "한국어 형태소 분석").
요건의 복합명사(한 어절, `결제시스템`)는 나뉘어도 한 낱말로 세고 모든 부분이 있어야 맞는다 — 앞부분만 같은 `결제 화면`은 맞지 않는다 (2026-11-17, 쌍 39/40, `search.md` "복합명사 낱말 일치").

- 수 표현: "3천", "1만 2천", "1.5만"(단위가 붙은 숫자), "두 배"·"세 명"·"열한 개"(고유어 수 + 단위 명사), "삼천"·"이십만"(한자어 수에 단위 글자가 있을 때), "절반"(50).
  단위 명사 목록(`COUNTERS`)과 뒤에 조사·끝이 오는지를 본다. "한"만 있는 경우("한 번")는 관용 표현이 많아 수로 보지 않는다. "만큼"은 만이 아니다.
  이 값은 `number_not_in_evidence`와 수정안의 `number` 거부에 쓴다(근거 "2배"와 문서 "두 배"는 같은 수).
- 기술명 별칭: 파이썬/Python, 자바스크립트/JavaScript, 쿠버네티스/K8s 같은 표(`TECH_ALIASES`). 요건 가시성·`keyword_list_unsupported`·수정안 `new_term`이 표기를 하나로 맞춰 비교한다. 공간 별칭 사전(c0021 `workspace_settings.tech_aliases`, 설정 화면 "작업 공간")이 이 표 위에 합쳐진다 (같은 대표 표기는 대신하고, 사용자 표기가 이긴다). 요건 가시성에는 참고용 판정 모델(`visibility-judge`, `models.md` "판정 모델")을 따로 부를 수 있다.
  "자바"는 "자바스크립트" 안에서는 따로 세지 않는다. 문서 표기가 공고와 다르면(`spelling_differs`) 공고 표기를 같이 적으라는 안내를 붙인다(판정은 그대로 `met_visible`).

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
단어 일치가 기준선이다. 교체 가능한 판정 모델(게이트웨이 뒤, 명시 라우트 `judge_visibility`)은 참고 의견으로만 붙는다 (`models.md` "판정 모델").

웹: 문서 화면 검사 탭 아래 "판정 모델로 확인"(`POST /core/applications/{id}/visibility-judge {document_id}`). 저장한 글만 보낸다(저장하지 않은 편집은 안 보냄을 알림).
요건마다 판정 모델 의견(근거 보임/안 보임/판단 어려움)·기준선 판정·"기준선과 다름"·이유를 보이고, 인용은 그 문단 글 안에서 `<mark>`로 강조하며 "문단 N"으로 그 문단에 간다.
호출 수·캐시에서 꺼낸 수(비용 없음)·비용(구독 환산 표시)·보내지 않은 제한 문단 수를 적는다. 검사 결과·충족은 바뀌지 않는다.

## AI 수정안 (§17.11, §17.17, §17.19)

1. `documents.ai`(egress) 권한, 용도 `doc_suggest`, 자료 등급 `private`로 게이트웨이를 지난다(전송 정책·단가·예산·장부).
   보내는 것: 대상 문단, 그 문단에 연결된 확인된 주장 문장, 공고 회사·직무·요건, 문항, 사용자 요청(500자). 프롬프트는 "사실을 더하지 말고, 모르면 질문하라"이다.
2. 모델 출력은 `{suggestions: [{block_id, text, reason}], questions}`. 없는 문단·같은 글·중복은 버린다(`dropped`). `questions`는 사용자에게 그대로 보여준다.
3. 코드 검토(`review_suggestion`) — 원문 + 연결 근거와 비교해 다음이 있으면 `rejected`(사유와 함께 저장, 적용 불가):
   `number`(새 숫자), `role`(새 주도·총괄 표현), `organization`(새 조직명), `new_term`(새 영문 고유명사·기술명), `ats_manipulation`(숨김 문자·지시문·키워드 반복).
4. 사용자가 `apply {base_revision}` → 해당 문단만 바꾼 새 revision(`origin = ai_suggestion`, 문단 `origin = ai`). 근거 연결은 그대로.
   문단이 그사이 바뀌었으면 `suggestion_stale`. 문서 보기의 `ai_ratio`가 AI 수정 문단의 글자 비율이다.
5. 수정안은 브레인에 쓰지 않는다. 주장·근거 테이블에 행이 생기지 않고, 수정안 ID를 근거로 걸 수도 없다.
6. 멱등: 요청에 `idempotency_key`를 주면 `(document_id, idempotency_key, block_id)` UNIQUE로 저장한다. 같은 키를 다시 보내면 모델을 부르지 않고 저장된 수정안을 `replayed: true`로 돌려준다(동시 요청의 UNIQUE 충돌도 같은 길로). 웹은 응답을 못 받은 같은 요청을 다시 보낼 때 같은 키를 쓴다.
7. 일괄 수정안 workflow `suggest_document_batch` (`documents.ai`): `{application_id, document_id, base_revision, block_ids?, instruction?}` (지원 문서만). 문단마다 한 단계로 `StepContext.model`을 불러 비용이 실행(run)에 쌓이고,
   문단마다 `effect_key("block:{id}")`를 멱등 키로 쓰므로 중간에 멈춘 실행을 이어도 이미 만든 문단의 수정안을 다시 만들지 않는다. 문서가 그사이 바뀌면 `revision_conflict`로 멈춘다.
   실행 기록에는 문단 ID·개수만 남기고 글·질문 문장은 넣지 않는다(결과: `{blocks, proposed, rejected, questions}`).
8. 요청 기록 `document_suggestion_requests` (c0016, SELECT/INSERT만): 요청 한 번(직접 요청이든 일괄 실행의 문단 하나든)마다 한 행.
   기준 revision, 문단 ID, 모델 질문(`questions`), 제안·거부·버림 수, 실행 ID, 모델 호출 ID. 수정안 행과 같은 트랜잭션에 쓴다.
   멱등 키가 있으면 `(document_id, idempotency_key)` UNIQUE라 수정안이 0개여도 기록이 남고, 재개·재시도는 이 행을 보고 모델을 다시 부르지 않는다
   (c0016 전 수정안 행만 있는 키는 예전처럼 수정안 행으로 돌려준다). 웹 수정안 탭은 "지난 요청에서 AI가 물어본 것"으로 질문을 보여 준다(일괄 실행 표시 포함).

## 제출본 (§17.12)

- `approve` 권한만(`applications.submit`). 요청에 사용자가 확인한 문서별 revision 번호를 넣는다. 하나라도 최신과 다르면 409 `stale` 목록.
- 스냅샷: 문서마다 revision ID·번호·블록·제출 문자열·글자 수·설정, 연결된 주장의 **그때 문장**(상태·기여·경험 정보 포함),
  공고의 그때 `fields`·`confirmed_version_id`·`pending_changes`, 당시 검사 결과. 전체의 `content_hash`.
- `application_submissions`는 SELECT/INSERT만. 이후 주장 문장·공고 항목을 고쳐도 제출본은 바뀌지 않는다(시험으로 확인).
- 확인 대기 중인 공고 변경(`pending_changes`)이 있으면 스냅샷에 같이 남는다.
- 파일로 내보내기(c0016): 제목은 지원 제목, 첫 줄에 `제출본 · 고정 시각 · 내용 해시 앞 12자`, 문서마다 소제목(문서 제목)과 `문항: …` 메모를 단다.
  문서 내보내기와 같은 writer(`domain/document_export.py`의 `sections`)라 escape·DOCX 형식·검사가 같다. 스냅샷의 제출 문자열(`text`)을 줄 단위로 쓰고 지금 문서는 읽지 않는다.

### 제출 전 민감 정보 재검사

- 지원 문서들의 최신 revision에서 찾는다(`domain/sensitive.py`, 정규식): 주민등록번호(`resident_registration_number`, error), 본인 것이 아닌 휴대전화·지역번호 전화(`phone_number`),
  본인 것이 아닌 이메일(`email_address`), "대외비·기밀·사내한정·confidential·internal only" 같은 표시(`company_confidential`). "기밀성"은 표시가 아니다.
- 본인 연락처는 `document_settings.own_contacts`(전화는 숫자만·+82는 0으로, 이메일은 소문자로 비교). 경고의 값은 가려서(`detail.masked`) 보여 준다.
- 경고가 있으면 제출에 `sensitive_ack` = `sensitive_hash`(경고 목록의 sha256)가 있어야 한다. 없거나 다르면 409 `{code: "sensitive_unacknowledged", sensitive_hash, findings}`.
  아무것도 지우지 않는다. 승인 묶음으로 제출하면 `sensitive_ack`도 승인 내용 해시에 들어간다(경고가 있을 때만 넣으므로 예전 승인 해시는 그대로).
- 확인하고 낸 경고는 제출본 `checks.sensitive`에 남는다.

### 전형 단계 (`application_stages`)

- 제출본(`submission_id`)에 묶인 단계: `kind` document/test/assignment/interview/final/other, 이름(기본: 서류·테스트·과제·면접·최종·기타), 일정, `outcome` pending/passed/failed/withdrawn/cancelled, `decided_at`, 메모.
  결과를 고르면 `decided_at`을 채우고 pending으로 돌리면 지운다(`ck_application_stages_decided`). 지원마다 30개까지.
- 시스템은 결과를 기록만 한다. 원인 칸이 없고, 단계 결과로 지원 상태를 자동으로 바꾸지 않는다.

## 내보내기 (Markdown · DOCX · PDF)

- `md`: 제목 + 문단. 줄 앞의 Markdown 기호와 `<`는 escape해 글이 서식으로 바뀌지 않게 한다.
- `docx`: 표준 라이브러리 `zipfile`로 최소 WordprocessingML(문단·제목, 굵게만)을 쓴다. 시각은 고정해 같은 글이면 같은 파일이다. python-docx 같은 의존성은 넣지 않았다.
- PDF: 서버는 인쇄용 HTML(`format=html`, 스크립트·inline style 없음, 인쇄 CSS만)을 만들고 웹이 새 창에서 브라우저 인쇄("PDF로 저장")를 연다.
  한글 글꼴을 서버 PDF 라이브러리에 싣는 비용(수 MB 글꼴 + 의존성)을 피하려는 선택이다. 파일 이름은 `….pdf.html`.
- 만든 파일을 다시 읽어 검사한다: DOCX는 XML에서 숨김(`w:vanish`·`webHidden`)·흰 글씨·크기 0/1 서식과 본문 글을, HTML은 원본과 보이는 글을 본다. 그 위에 숨김 문자·심사 지시문 검사와 민감 정보 검사.
  경고만 하고(`export/check`로 먼저 보여 준다) 파일은 그대로 준다. 웹은 경고가 있으면 확인 체크를 받은 뒤 받게 한다.
  문서 화면과 제출본 화면이 같은 시트(`components/doc/ExportSheet.tsx`)를 쓴다.

### 올린 DOCX·PDF 숨김 글 검사 (c0016)

다른 도구로 만든 이력서·포트폴리오를 내기 전에 본다(`domain/hidden_text.py`). 경고만 하고 파일을 고치거나 막지 않으며, 검사 경로는 파일을 저장하지 않는다.

- DOCX(표준 라이브러리 `zipfile` + `xml.etree`): 본문·머리글·바닥글·각주의 run마다 `w:vanish`/`specVanish`(스타일 상속 포함) → `hidden_text_vanish`,
  `webHidden` → `hidden_text_invisible`, 흰 글씨(`FFFFFF`·`white`, 어두운 음영 위는 제외) → `hidden_text_white`, 1pt 미만(`w:sz` < 2) → `hidden_text_tiny`,
  쪽 밖으로 민 글상자·frame(음수·쪽 크기 넘는 위치) → `hidden_text_outside_page`. DTD/ENTITY가 있는 XML, 압축 해제 크기·항목 수 초과는 거부한다.
- PDF(`pypdf`, 이미 쓰던 의존성): 내용 스트림을 따라가며 그래픽 상태(q/Q, 색 `rg/g/k/sc/scn`, 글자 렌더 모드 `Tr`, 글자 크기·행렬)를 추적한다.
  렌더 모드 3·7 → `invisible`, 흰 채움 → `white`, 실제 크기 1pt 미만 → `tiny`, 보이는 상자(CropBox) 밖 → `outside_page`. 100쪽까지 보고 나머지는 `pages_not_checked`, 글을 못 읽은 쪽은 `pages_unreadable`.
  암호 PDF는 422 `pdf_encrypted`.
- PDF 배경 색 대조 (c0023): 흰 채움을 무조건 경고하지 않고 **글 밑에 먼저 그린 배경**과 비교한다.
  - 쪽마다 채운 도형(`re`·`m/l/c/v/y` 경로 + `f/F/f*/B/b…`)을 (상자, 채움 색) 목록으로 쌓는다. 쪽 전체를 덮는 채움은 목록을 비운다. 상한 2000개(넘으면 그 뒤는 모르는 배경).
    경로는 상자(bbox)로 근사한다(→ 아래 "남은 틈 보강"에서 다각형으로). 잘라내기(`W`)·form XObject(`/Matrix` × 현재 행렬)·`q/Q`를 따른다.
  - 그림자(`sh`)·인라인 이미지·이미지 XObject는 "모르는 배경"으로 쌓는다. 그 위의 글은 색 비교를 하지 않는다 (보이는지 알 수 없음, 경고 없음).
  - 투명도 `gs /ca`: 0.05 이하면 보이지 않는 글(`invisible`), 0.95 미만이면 배경을 모르는 것으로 본다(→ 아래 "남은 틈 보강"에서 섞은 색으로).
  - 글의 기준점(기준선 위 글자 크기 0.3배)에서 가장 위에 그린 배경을 고른다. 없으면 흰 쪽. 채움 색과 배경의 채널 차가 모두 0.06 이하면 숨긴 글:
    배경이 흰색이면 `hidden_text_white`, 아니면 `hidden_text_same_color`(새 코드). 어두운 상자 위의 흰 글씨는 이제 경고하지 않는다.
- PDF 남은 틈 보강 (backlog4, migration 없음 — 검사 코드만):
  - **모양**: 칠한 경로를 경계 상자가 아니라 장치 좌표 다각형(곡선 `c/v/y`는 8조각 꺾은선, `h`로 닫기)과 채움 규칙(`f`·`B`는 0이 아닌 감김 수, `f*`·`B*`는 짝홀)으로 기억한다.
    축에 맞춘 사각형은 상자 그대로. 잘라내기(`W`·`W*`)도 다각형으로 쌓아 "도형 안 ∩ 모든 잘라내기 안"만 칠한 곳이다. 점이 2000개를 넘는 경로만 경계 상자로 줄이고(예전 근사), 그런 도형은 덮기 판정에 쓰지 않는다.
    삼각형·원의 경계 상자 안이지만 도형 밖에 있는 흰 글은 이제 흰 바탕 위로 본다. 짝홀 구멍 안 글도 마찬가지.
  - **반투명 섞기**: ExtGState 상수 투명도 `/ca`(채움)·`/CA`(선)를 Normal 혼합으로 섞는다 — 반투명 채움은 그 점의 아래 색과 `a·위 + (1−a)·아래`, 반투명 글은 섞인 글자 색을 배경과 비교한다
    (예: 5.5% 검은 글 = 흰 바탕에서 `hidden_text_white`, 50% 검은 상자 위 0.5 회색 글 = `hidden_text_same_color`). `/BM`이 Normal·Compatible이 아니거나 `/SMask`가 있으면 섞인 색을 모르는 것으로 본다(비교하지 않음).
    선 글(`Tr` 1·2·5·6)은 선 색·`/CA`도 본다: 채움+선 글에서 채움만 투명하면 선이 보이므로 보이지 않는 글이 아니다.
  - **글 뒤에 덮은 것** `hidden_text_covered`(새 코드, 경고): 글 그리기마다 글자 상자(폭은 글꼴 글자 폭 + `Tc`·`Tw`·`Tz`, 높이는 기준선부터 글자 크기 0.7배)에 격자 점(가로 2~24 × 세로 3)을 두고,
    **그 뒤에** 칠한 불투명(`/ca` ≥ 0.95, Normal, soft mask 없음, 정확한 모양) 도형이나 마스크 없는 이미지(`/ImageMask`·`/Mask`·`/SMask` 없음, 인라인 이미지 포함)가 모든 점을 덮으면 가린 글이다.
    색은 상관없다(흰 상자·사진 모두). 일부만 덮거나 반투명·곱하기 혼합이면 경고 없음. 이미 다른 이유로 숨긴 글은 두 번 세지 않는다. 쪽마다 비교 상한이 있다(넘으면 그 뒤 덮기는 보지 않음).
    한계: 격자 점 사이의 아주 작은 구멍은 놓칠 수 있다. 주석 모양(appearance)·음영(`sh`)은 덮개로 보지 않는다.
  - **연산마다 판정**: pypdf는 같은 줄 글을 한 덩어리로 돌려주므로, 글 그리기 연산(`Tj`·`TJ`·`'`·`"`) 하나의 글·폭을 pypdf 글꼴 해석(`pypdf.generic._font.Font`, 내부 API)으로 따로 읽어 판정·예시·개수를 그 연산 단위로 낸다.
    보이는 글과 한 줄에 섞인 흰 글 하나도 그 글만 예시가 되고(`in_hidden_text`도 그 글로), 같은 줄 다음 연산의 위치는 앞 연산의 폭만큼 옮겨 본다(pypdf의 글 행렬은 글자 폭만큼 나아가지 않는다). `'`·`"`는 `TL`만큼 다음 줄에서 본다.
    pypdf 내부가 바뀌어 해석을 못 쓰면 그 연산만 예전처럼 덩어리 글을 예시로 쓰고 폭은 바이트당 글자 크기 0.6배로 어림한다(같은 덩어리·종류는 한 번만 센다). pypdf를 올릴 때 `test_documents_hidden_more.py`가 이 경로를 확인한다.
- PDF 주석·양식 칸 (c0023): 쪽의 `/Annots`에서 `/Contents`와 위젯(양식 칸)의 값 `/V`(부모 칸 사슬 따라)를 읽어 전체 글 검사(지시문·키워드)에 넣는다. Popup은 건너뛴다.
  - 숨김 플래그(`/F`의 Invisible 1·Hidden 2·NoView 32), 크기 0인 상자, 쪽 밖 상자 → `hidden_text_annotation` (경고).
  - 그 밖의 주석 글(FreeText·위젯 제외, 예: 메모 아이콘 안의 글) → `annotation_text` (정보, `severity = info`). 인쇄본에는 안 보일 수 있는 글이라 알려만 준다.
  - AcroForm에만 있고 어느 쪽에도 놓이지 않은 칸 → `hidden_text_annotation`. 모든 쪽을 본 경우에만, 칸 수 상한 안에서.
- OCR은 범위 밖이다. 이미지로 된 글은 읽지 않으며, 이미지 위 글자의 색도 비교하지 않는다.
- 숨김 글이 아닌 전체 글에도 숨김 문자·심사 지시문·키워드 반복 검사를 돌린다. 지시문이 숨긴 글 안에 있으면 `detail.in_hidden_text`.
- 각 경고는 `detail.count`와 앞 몇 곳의 `samples`(쪽·위치·글 일부)를 준다. 웹: 공용 문서 화면의 "제출할 파일 숨김 글 검사" 카드.

### DOCX 원본 (c0023)

브레인 자료로 DOCX를 올릴 수 있다 (`uploads.ALLOWED_MIME_TYPES`에 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`).

- 형식 판별: `PK\x03\x04`로 시작하고 zip 안에 `word/document.xml`이 있을 때만 DOCX. 다른 zip은 415.
- 파서 `adapters/parsers.py::DocxTextParser` (표준 라이브러리, 의존성 없음). 블록 순서: 본문 문단 → 표(행마다 칸을 ` | `로 이은 한 블록) → 글상자 안 문단(따로 블록) → 각주·미주(보통 종류만).
  `mc:Fallback`(글상자 사본)·`w:del`(삭제 표시 글)은 읽지 않는다. 블록 위치는 글 안의 문자 위치이고 `page`는 없다(DOCX에는 쪽이 정해져 있지 않다). 블록 상한 5000.
- 안전 장치는 숨김 글 검사와 한 벌(`domain/docx_zip.py`): 항목 수 2000, 부분 20MB·합계 60MB(중앙 디렉터리 값을 먼저 보고, 실제로 풀 때도 상한 + 1바이트까지만 읽어 다시 확인),
  같은 이름 두 번 거부, DTD/ENTITY 거부, 메모리에서만 읽는다. 업로드 크기 상한(`CAREER_MAX_UPLOAD_MB`)은 그대로.
- 깨진 파일은 파싱 실패(`malformed`), 너무 복잡·큰 파일은 `file_too_complex`·`file_too_large`.
- 올린 DOCX도 숨김 글 검사를 원본 경로(`GET /sources/{id}/hidden-text`)로 다시 할 수 있다. 기본은 숨김 글을 원본 블록에 넣는다 (검사는 경고만).
- **숨김 run 빼기 (c0029)**: 업로드 폼 `hidden_text=exclude`(기본 `keep`)면 숨김 글 검사가 `hidden_text_vanish`로 세는 run(`w:vanish`·`specVanish`·`webHidden`, 글자·문단 스타일 상속 포함 — `hidden_text.docx_run_kinds` 한 규칙)을 블록에서 뺀다(`DocxTextParser.parse_visible`, 포트 `HiddenTextParser`).
  값은 그 파싱 실행에 남는다(`parse_runs.hidden_text`, 응답 `UploadOut.hidden_text`·`ParseOut.hidden_text`). DOCX가 아니면 `keep`으로 저장하고, 모르는 값은 422 `invalid_hidden_text`.
  같은 파일을 다시 올리면 처음 파싱의 값을 그대로 돌려준다(다시 파싱하지 않음). 숨김 글 검사는 저장한 원본 파일을 보므로 뺀 글도 그대로 경고한다. 흰 글씨·작은 글자 run은 빼지 않는다(보이는 서식일 수 있어 경고만). 웹: 자료 올리기에서 DOCX일 때 "숨김 서식 글은 빼고 읽기".
- **쪽 번호는 만들지 않는다**: DOCX 블록의 `page`는 언제나 `None`이다. Word의 쪽 나눔은 그리는 프로그램·글꼴·용지가 정하고 파일에는 마지막으로 저장할 때의 힌트(`w:lastRenderedPageBreak`)만 있어, 이것으로 쪽을 매기면 사람이 보는 쪽과 다를 수 있다 (plan §9 "없는 좌표를 만들어 넣지 않는다"). 근거 위치는 블록 순서(`ordinal`)와 글 안 문자 위치다.

### 서버 PDF를 만들지 않는 이유 (c0016 검토)

가벼우면서 라이선스가 깨끗한 선택지가 없어 브라우저 인쇄를 유지한다.

| 후보 | 걸리는 점 |
|---|---|
| WeasyPrint | Pango·cairo·HarfBuzz 같은 시스템 라이브러리가 필요하다(설치 문서·컨테이너가 무거워진다). 한글 글꼴도 따로 설치 |
| ReportLab / fpdf2 + 임베드 글꼴 | 한글 TTF를 레포나 패키지에 실어야 한다. Noto Sans KR/CJK(OFL)는 굵기 하나에 수 MB~16MB, Pretendard·나눔(OFL)도 부분 집합으로 줄여도 굵기마다 수 MB. fpdf2는 LGPL |
| ReportLab 내장 CID 글꼴(HYGothic·HYSMyeongJo) | 글꼴을 넣지 않고 이름만 쓴다 → 보는 프로그램에 그 글꼴이 없으면 대체 글꼴로 바뀐다(모양·줄바꿈이 사람마다 달라진다) |
| headless Chromium | 수백 MB 런타임 |

브라우저 인쇄는 사용자 컴퓨터의 한글 글꼴을 쓰므로 추가 의존성이 없고, 서버가 만든 인쇄용 HTML은 숨김 글 검사를 거친다.
대신 쪽 나눔·여백은 브라우저마다 조금 다르다. 정해진 쪽수가 꼭 필요해지면 그때 Noto Sans KR 부분 집합 + fpdf2/ReportLab을 다시 검토한다.

## plan과 다른 점

- §7의 `artifacts / artifact_versions / artifact_claims` 대신 `application_documents / document_revisions / document_claims`.
  공용 문서는 별도 표가 아니라 같은 표의 `application_id IS NULL` 행이다(revision·근거·수정안 규칙을 한 벌로 유지).
- §7의 `application_stages`는 제출본에 묶었다(제출 전 단계는 없다). 지원 상태는 여전히 `applications.status`가 원장이고 단계는 자동으로 바꾸지 않는다.
- §11 승인 묶음(행위·대상·내용 해시·만료·멱등 키)은 O08 범위다. 지금 제출은 `approve` scope + 확인한 revision 번호 일치로 막는다.
- 문장 단위 근거는 문단 블록 안의 위치(spans)다. `document_claims`는 여전히 문단 단위 행이다(span 주장도 문단 `claim_ids`에 합쳐 들어간다).
- 상투어 목록은 일반 "공간 설정" 표가 아니라 문서 전용 `document_settings`에 둔다.

## 한계

- 한국어 처리는 조사 떼기·불용어와 규칙 기반 수 표현·기술명 별칭 표다. 형태소 분석이 없어 놓치거나 과하게 잡는다
  (예: 단위 명사 목록에 없는 "두 군데", 띄어 쓰지 않은 "세명이서"는 잡고 "한 명"은 일부러 뺀다). 기본 별칭 표는 코드에 있고, 사용자는 공간 별칭 사전으로 더하거나 대신한다.
- 역할 대조는 정해진 단어 목록이다("맡아 이끌었다" 같은 표현은 놓칠 수 있다).
- 서버가 PDF를 만들지 않는다(브라우저 인쇄, 위 검토).
- 올린 PDF 검사의 배경 대조(c0023)는 **글보다 먼저 그린** 채움만 본다. 글 위에 나중에 덮은 불투명 도형으로 가린 글은 놓친다. 반투명 섞임은 계산하지 않고(모르는 배경),
  사각형이 아닌 경로는 상자로 근사한다. pypdf가 같은 줄의 글을 합쳐 주면 한 덩어리로 판정한다. 이미지로 된 글은 OCR이 없어 보지 않는다.
  DOCX는 테마 색·조건부 서식·글자 색과 같은 강조 색 같은 경우를 다 따지지 않는다.
- 숨김 글 검사는 원본(`sources`)으로 올린 파일과 검사 경로(`POST /file-checks/hidden-text`)에서 한다. c0023부터 DOCX도 원본으로 올릴 수 있어 `GET /sources/{id}/hidden-text`로 다시 검사한다.
- 공용 문서의 revision은 "최근 활동"에 지원 제목 대신 "공용 문서"로 나온다(c0016, `refs.shared = "true"`, 활동 목록이 지원과 left join한다).
- 웹 편집기에서 문장 근거를 끊을 때, 그 주장이 문단 근거에도 있고 다른 문장이 쓰지 않으면 "문장만 끊기 / 문단 근거에서도 빼기"를 묻는다(c0016).
