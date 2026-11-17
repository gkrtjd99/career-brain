# DB·공간 격리·migration (O02, O03)

## 소유 영역 (schema)

| schema | 소유 | 내용 |
|---|---|---|
| `core` | career-brain `packages/core` | workspaces, actors, memberships, sources, source_versions, parse_runs, document_blocks, jobs, 이후 모든 사용자 자료 |
| `personal` | career-brain `apps/personal-api` | 개인용 로그인(API 토큰) |
| `platform` | career-service (향후) | 계정·플랜·결제·운영 |

각 schema는 자기 `alembic_version` 테이블을 가진다. 다른 영역의 테이블을 ALTER하지 않는다.

## DB 역할

| 역할 | 용도 |
|---|---|
| 소유자 (`career`, 로컬) | migration 실행. 테이블 소유자 |
| `career_app` (NOLOGIN) | 런타임 권한 묶음. migration이 테이블별로 GRANT |
| `career_api` (LOGIN, `career_app` 멤버) | API·worker 접속 계정. superuser·BYPASSRLS·소유자 아님 |

## 공간 격리

1. 앱은 `Database.workspace_session(workspace_id)`로만 사용자 자료를 다룬다.
   트랜잭션 로컬 `set_config('app.workspace_id', …, true)` → commit/rollback 시 사라진다.
2. `workspace_id`가 있는 모든 테이블은 `ENABLE` + `FORCE ROW LEVEL SECURITY`와
   `workspace_isolation` 정책을 갖는다 (`career_core.db.rls.workspace_isolation_sql`).
   문맥이 없으면 0행, 다른 공간으로 INSERT/UPDATE하면 오류.
3. 다른 공간 소유 테이블 참조는 `(workspace_id, parent_id) → parent(workspace_id, id)` 복합 FK
   (`career_core.db.scoping`). UUID만 맞는 교차 연결을 DB가 거부한다.
4. workspace는 요청 body·path·query로 받지 않는다. `IdentityResolver`가 확정한다.
5. 예외 (ENABLE만, FORCE 없음 — 소유자 권한의 좁은 함수 하나로만 공간 문맥 없이 접근):
   - `personal.api_tokens` ← `personal.lookup_api_token(hash)` (토큰 → 공간·actor)
   - `core.jobs` ← `core.claim_job(kinds, lease)` (worker가 다음 작업 하나를 잠금)
   앱 역할의 직접 조회는 여전히 RLS로 막힌다.
6. 주기 유지보수용 `core.maintenance_workspace_ids()` (c0015, SECURITY DEFINER): 모든 공간의 **ID만**
   돌려준다. `workspaces`는 FORCE라 소유자도 정책을 받으므로, 이 함수의 `SET career.maintenance_scan = on`
   안에서만 참이 되는 **소유자 전용** SELECT 정책(`maintenance_scan`)을 하나 더 둔다. 앱 역할이 같은
   설정을 켜도 그 정책 대상이 아니라 0행이다 (`test_jobs.py`).
7. `core.jobs` trigger `jobs_notify_ready` (c0015): 대기 행이 생기면 `NOTIFY career_jobs, '<kind> <초>'`.
   LISTEN은 권한 검사가 없으므로 내용에 공간·대상 ID를 넣지 않는다.

회귀 시험: `workspace_isolation_problems()`가 새 테이블의 RLS 누락을 잡는다.

## migration 실행

`career_core.db.migrate.upgrade(url, chains)` 하나가 `pg_advisory_lock`을 잡고 core → (personal | platform)
순서로 실행한다. API 시작 시 자동 실행하지 않는다.

```bash
uv run personal-api migrate
```

운영에서 소유자가 superuser가 아니면 `FORCE RLS` 때문에 데이터 migration도 공간 문맥이 필요하다.

## 원본·파싱 (O03)

```text
업로드 → 크기·실제 내용 MIME 검사 → sha256 → 같은 공간에 같은 해시면 기존 항목 반환
       → 파일 저장(공간별 디렉터리, 내용 주소) → sources / source_versions / parse_runs / jobs
worker → claim_job → 로컬 추출(텍스트·HTML·텍스트 PDF) → document_blocks
       → 텍스트 없는 쪽: 외부 파서 미설정 → partial/failed (ocr_unavailable)
                        EgressPolicy 거부 → partial/failed (egress_denied)
                        허용 → 제출, provider_job_id 저장 → 이후 상태 조회만
```

- 중복 방지: `UNIQUE (workspace_id, sha256)`와 `UNIQUE (workspace_id, source_version_id, pipeline_version)`.
  공간이 다르면 같은 파일도 별개로 저장한다(다른 공간에 파일이 있는지 드러내지 않는다).
- 부분 파싱: `pages_total`, `missing_pages`, `error_code`로 표시한다. 요청한 쪽과 제공자가 돌려준 쪽을 비교한다.
- 외부 전송: `restricted` 등급은 제공자 설정과 무관하게 보내지 않는다. 설정하지 않은 제공자도 거부.
- 작업 큐 행에는 원문을 넣지 않고 대상 ID만 둔다. 실패 기록에는 예외 종류만 남긴다.
- 외부 OCR 어댑터(PaddleOCR·MinerU 등)는 아직 연결하지 않았다. `ParserProvider` 포트로 붙인다.

## 모델 호출 장부 (c0003)

`core.model_calls` — 모델 호출 1건 = 1행. RLS FORCE, 앱 role 권한은 `SELECT, INSERT`뿐이다(고쳐서 비용을 줄일 수 없다).

- 기록: 용도, 엔드포인트, 요청/응답 모델, 상태(`ok`/`error`/`denied`), 오류 코드, 토큰(입력·캐시 읽기·캐시 쓰기·출력·reasoning), 과금 방식(`metered`/`subscription`/`free`), 비용과 근거(`provider_reported`/`metered`/`estimated`/`list_price_equivalent`/`unknown`/`none`), 단가 출처 참조, 지연시간, 제공자 요청 ID.
- 기록하지 않음: 프롬프트·응답 본문, 키.
- 월 예산은 이번 달(UTC) `billing = 'metered'` 행의 `cost_usd` 합으로 본다. 동시에 여러 호출이 출발하면 한도를 조금 넘을 수 있다(사전 확인 방식). 서비스는 `BudgetPolicy`(예약·정산)를 주입해 정확히 막는다.

## 경험·주장·근거·인터뷰 (c0004)

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `source_spans` | 원문 블록의 `[start_char, end_char)` 범위와 그 인용. `length(quote) = end - start` CHECK | `SELECT, INSERT` |
| `experiences` | 경험 묶음(제목·조직·역할·기간 원문). `origin`: `ai_extraction`/`interview`/`user`. c0031: 종류·상위(한 단계)·달 단위 기간·태그 | 전체 |
| `extraction_runs` | 파싱 결과 1건에 대한 AI 추출 1회. 보낸 블록 수, 만든/버린 주장 수, `model_call_id` | 전체 |
| `claims` | 주장 한 문장. `status`, `kind`, `contribution`(`personal`/`team`/`unknown`) | 전체 |
| `claim_evidence` | 주장 ↔ 근거. `source_span` 또는 `interview_answer` 중 정확히 하나 | 전체 |
| `interview_sessions` / `interview_questions` | 인터뷰와 질문(템플릿 또는 AI, AI면 `model_call_id`) | 전체 |
| `interview_answers` | 사용자가 쓴 답 원문 | `SELECT, INSERT` |

- 주장 상태: `extracted`(후보) → `self_confirmed`(본인 확인) / `evidence_supported`(본인 확인 + 원문 위치) / `disputed` / `retired`.
  `ck_claims_confirmed`: 확정 상태면 `confirmed_by`, `confirmed_at`이 있고 `contribution <> 'unknown'`.
- 확정은 `approve` 범위가 있는 사람만 한다. 확인 권한 없는 사람이 확정된 주장을 고치면 다시 `extracted`가 된다.
- 근거 칸은 사용자 원문 위치와 사용자가 쓴 답만 가리킨다(`ck_claim_evidence_kind`). AI가 만든 텍스트를 근거로 연결할 방법이 없다.
- 추출은 "고르기"다. 모델은 블록 번호와 인용을 내고, 코드가 원문에서 그 인용을 찾는다(정확히 일치, 또는 공백 차이만 허용).
  못 찾은 주장은 버리고(`claims_dropped`), 근거 있는 주장이 하나도 없는 경험은 만들지 않는다. AI가 붙인 `contribution`은 참고값이고 상태는 항상 `extracted`로 시작한다.
- 추출은 job(`kind = 'extract'`)으로 돈다. 원본의 외부 전송 등급이 그대로 모델 요청 등급이 되며, `restricted`는 게이트웨이에서 거부된다.
  한 번에 보내는 본문은 40,000자까지이고, 잘렸으면 `partial`.
- 모델 출력·프롬프트 본문은 저장하지 않는다. 추출 결과로 남는 것은 원문 인용과 모델이 쓴 주장 문장(후보)뿐이다.
- 위키는 테이블이 아니라 보기다. `GET /api/core/wiki/experiences/{id}`(JSON)와 `/markdown`이 확정 주장(`self_confirmed`/`evidence_supported`)만 종류별로 묶고, 각 주장 아래 원문 인용·인터뷰 답을 붙인다. 후보는 "확인 대기 N건"으로만 표시한다.

## 검색 (c0005)

- 확장: `pg_trgm`, `vector`(pgvector). DB 소유자가 만들 수 없으면 관리자가 미리 `CREATE EXTENSION`.
- trigram GIN 색인: `claims.statement`, `document_blocks.text`, `interview_answers.answer`.
- `core.embeddings` — 대상(`target_kind` ∈ claim/block/answer/job, `target_id`)·공간(`space`)마다 벡터 1개. 원문은 두지 않고 `content_hash`(sha256)만. `vector_dims(embedding) = dims` CHECK. RLS FORCE.
  대상 테이블에 FK가 없다(여러 종류를 가리킨다). 대상이 사라진 벡터는 검색 조인에서 자연히 빠진다.
- 상세: `docs/architecture/search.md`.

## 채용 공고 (c0006)

- `core.job_postings` — 공간마다 `canonical_url` UNIQUE (정규화: 소문자 호스트, 추적 파라미터·조각·기본 포트·끝 슬래시 제거, 쿼리 정렬). `status` draft/saved/archived. 확정한 값은 `fields`, 항목별 근거는 `evidence`(structured 경로·quote 위치·user). `confirmed_version_id`, `pending_changes`(확정 후 바뀐 항목), saved면 `saved_by`·`saved_at` 필수 (`ck_job_postings_saved`). c0033: 회사 연결 `company_id` (아래 "회사 조사").
- `core.job_posting_versions` — 가져온 원문 스냅샷. **SELECT/INSERT만** (수정·삭제 불가). `(posting_id, version_no)`, `(posting_id, content_hash)` UNIQUE → 같은 본문이면 새 버전·LLM 호출 없음. `method` http/browser/manual_text, `adapter`, `extracted`, `missing`, `changed_fields`, `model_call_id`, `screenshot_key`(blob sha256).
- `core.job_fetch_runs` — 가져오기 시도 기록. `status` pending/done/listing/failed, `error_code`, `http_status`, `unchanged`, 목록 페이지면 `candidates`(따라가지 않음), `notes`.
- 확정하지 않은 초안은 7일 뒤 `purge_unconfirmed`로 정리 (버전·실행 기록은 FK CASCADE).
- 상세: `docs/architecture/postings.md`.

## 지원·문서 (c0007)

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `applications` | 저장한 공고 하나에 대한 지원. `posting_id`, 만들 때 확정돼 있던 `posting_version_id`, `status` draft/submitted/passed/rejected/withdrawn, `result_note`. 제출 이후 상태면 `submitted_at` 필수 (`ck_applications_submitted`) | 전체 |
| `application_documents` | 지원의 문서. `kind` question/resume/cover_letter/portfolio/other. 문항이면 `question_index`·`question_prompt`(복사본) 필수. `char_limit`·`byte_limit`·`count_rule`(공백·줄바꿈·인코딩). `current_revision`(0 = 본문 없음) | 전체 |
| `document_revisions` | 저장 1회 = 1행. `blocks`(문단 `{id, text, origin, claim_ids}`), `content_hash`, 저장 시점 `counts`, `origin` user/ai_suggestion(그러면 `suggestion_id` 필수), `reason`, `idempotency_key`. `(document_id, revision_no)`·`(document_id, idempotency_key)` UNIQUE | **SELECT, INSERT** |
| `document_claims` | revision 문단 ↔ `core.claims`. `(revision_id, block_id, claim_id)` UNIQUE | **SELECT, INSERT** |
| `document_suggestions` | AI 수정안. 원문·수정안·이유, `status` proposed/rejected/applied/dismissed, 거부면 `reject_reasons` 필수, 적용이면 `applied_revision_id` 필수, `model_call_id` | SELECT, INSERT, UPDATE |
| `application_submissions` | 실제 제출본. 지원마다 1건(`application_id` UNIQUE). 문서 본문·근거 주장 문장·공고 항목을 **복사**한 `documents`·`posting`, 당시 검사 결과 `checks`, `content_hash`, 제출 시점 `posting_version_id` | **SELECT, INSERT** |

- 모든 테이블 RLS FORCE, 공간 간 참조는 복합 FK. 다른 공간의 주장 ID를 문단에 걸면 앱이 `unknown_claim`으로, DB가 FK로 거부한다.
- 지울 수 없게 묶은 참조는 `NO ACTION`(문장 끝 검사): 제출본 → 지원·공고·공고 버전, 문단 근거 → 주장, 적용된 수정안 → revision. 공간 삭제의 연쇄 삭제는 막지 않는다.
- 저장 충돌: 문서 행을 `FOR UPDATE`로 잠그고 `base_revision = current_revision`일 때만 다음 번호로 넣는다. 다르면 409, 덮어쓰지 않는다.
- AI 수정안은 `claims`·`claim_evidence`에 쓰지 않는다. `claim_evidence`는 원래 원문 위치와 인터뷰 답만 가리키므로(`ck_claim_evidence_kind`) AI 글이 근거가 될 길이 없다.
- 상세: `docs/architecture/applications.md`.

## 학습 (c0009)

계획 문서 §7의 `assessment_requirements, study_items, attempts, reviews`를 아래 이름으로 구현했다. 모두 RLS FORCE, 공간 복합 FK.

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `study_requirements` | 저장 공고 요건 한 줄 (`field` requirements/preferred/process, 확정 당시 `text`). `(workspace_id, posting_id, field, text)` UNIQUE | `SELECT, INSERT` |
| `study_requirement_claims` | 요건 ↔ 주장 (사용자가 연결). `(requirement_id, claim_id)` UNIQUE | `SELECT, INSERT, DELETE` |
| `study_assessments` | 시험 준비 (`kind` coding_test/written_test/assignment/interview/certification/other, `status`, 일정은 모르면 비움) | 전체 |
| `study_items` | 학습 항목 (`status` planned/in_progress/done/dropped, `ck_study_items_completed`: done ⇔ `completed_at`) | 전체 |
| `study_sessions` | 학습 기록 (분 1–1440, 메모, 자료 제목·URL — 가져오지 않음) | 전체 |
| `study_attempts` | 응시·풀이 1회 (`result`, 점수 ≤ 만점, 시험 또는 항목 중 하나 이상) | `SELECT, INSERT` |
| `study_mistakes` | 오답 + 복습 상태 (`repetitions`, `interval_days`, `ease` 천분율, `due_at`). 부분 색인 `(workspace_id, due_at) WHERE status = 'open'` | 전체 |
| `study_reviews` | 복습 1회 (평가, 전후 간격·배율·다음 날짜) | `SELECT, INSERT` |
| `study_claim_candidates` | 학습 결과 → 사용자가 올린 확인 대기 주장 (`claim_id` UNIQUE) | `SELECT, INSERT` |

- 선택 연결(`requirement_id`, `assessment_id`, 후보의 `attempt_id`)은 `ON DELETE SET NULL (칸)` — 부모가 지워져도 `workspace_id`는 그대로 둔다 (PostgreSQL 15+).
- 학습 테이블은 `claims`·`experiences`에 쓰지 않는다. 예외는 사용자가 직접 올린 후보 1건이며 `extracted`로만 생긴다.
- 상세: `docs/architecture/study.md`.

## 실행·승인 (c0010)

계획 문서 §7의 `runs, run_events, approvals`. 모두 RLS FORCE, 공간 복합 FK. `outbox`는 아직 두지 않았다 (부수효과가 모두 DB 안의 멱등 usecase라서, `docs/architecture/runs.md`).

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `runs` | 실행 1건. `workflow`, `status`(queued/running/waiting_approval/interrupted/cancelled/failed/done), 재개 위치 `step`·`position`, `result`, `stop_requested`(cancel/interrupt), 한도 대기 `wait_reason`·`waits`·`retry_at`, `approval_id`, `error_code`, 비용 `cost_usd`·`cost_unknown`·`model_calls`, `resumes`, 시작한 actor의 `actor_kind`·`actor_scopes`, `idempotency_key` (`(workspace_id, idempotency_key)` UNIQUE). 끝 상태 ⇔ `finished_at` (`ck_runs_finished`), failed ⇒ `error_code` | `SELECT, INSERT, UPDATE` |
| `run_events` | 실행 사건 (`seq`, `kind` status/step/wait/resumed/error, 그때의 `status`·`step`, `code`, 짧은 `data`). `(run_id, seq)` UNIQUE | **SELECT, INSERT** |
| `approvals` | 승인 1건. 행위·대상·`target_version`·`payload_hash`(sha256)·`payload`·`summary`·만료, 요청자와 `requested_via`(human/agent), 결정자와 `decided_via`, 사용 `used_at`·`used_result_id`, 무효 사유. `(workspace_id, idempotency_key)` UNIQUE | `SELECT, INSERT, UPDATE` |

- `ck_approvals_human_decision`: `approved`·`used`면 `decided_by`·`decided_at`이 있고 `decided_via = 'human'`. AI(agent)가 승인 상태를 만들 길이 DB에도 없다.
- trigger `approvals_binding_immutable`: 행위·대상·대상 버전·내용·요청자·만료·공간은 만든 뒤 바꿀 수 없고, 닫힌 승인(used/rejected/invalidated/expired)은 다시 열 수 없다.
- 실행 ↔ 승인은 서로 선택 연결(`ON DELETE SET NULL (칸)`). 실행·승인은 앱 role이 지울 수 없다 (기록).
- 작업 큐 행은 `core.jobs(kind = 'run', target_id = runs.id)`. 승인 대기·중단 중에는 작업 행이 없다 (결정·재개 때 새로 넣는다).
- 원문은 넣지 않는다. 입력·위치·결과·사건에는 ID·번호·짧은 코드만.
- 상세: `docs/architecture/runs.md`, MCP: `docs/architecture/mcp.md`.

## 문서 추가 (c0012)

공용 문서·복사 출처·문장 단위 근거·수정안 멱등·문서 설정·전형 단계. 상세: `docs/architecture/applications.md`.

| 바뀐 것 | 내용 | 앱 role |
|---|---|---|
| `application_documents.application_id` | NULL 허용. NULL = 공용 문서. `ck_application_documents_shared_kind`: 공용 문서는 `question`일 수 없다. 부분 색인 `ix_application_documents_shared (workspace_id) WHERE application_id IS NULL` | (그대로) |
| `application_documents.source_document_id`·`source_revision_id` | 복사 출처. 둘 다 있거나 둘 다 없다(`ck_application_documents_source`). 복합 FK `(workspace_id, …)` → 문서·revision, `DEFERRABLE INITIALLY IMMEDIATE` (자기 참조 + 문서 ↔ revision 순환이라 복원 때 미룬다) | (그대로) |
| `document_revisions.blocks` | 문단에 선택 키 `spans: [{start, end, claim_ids}]`. 칸 변경 없음(JSONB) | |
| `document_suggestions.idempotency_key` | 요청 멱등 키. `uq_document_suggestions_idempotency (document_id, idempotency_key, block_id)` (NULL은 겹쳐도 된다) | |
| `document_settings` (새 표) | 공간마다 1행(`workspace_id` UNIQUE). `cliches` JSONB 배열(NULL = 기본 목록, `ck_document_settings_cliches`), `own_contacts` JSONB `{emails, phones}`, `updated_by` | SELECT, INSERT, UPDATE |
| `application_stages` (새 표) | 제출본에 묶인 전형 단계. `application_id`·`submission_id`(복합 FK, 제출본은 `NO ACTION`), `position`, `kind`, `name`, `scheduled_at`, `outcome`, `decided_at`(`ck_application_stages_decided`: pending ⇔ NULL), `note` | 전체 |

- 두 새 표 모두 RLS FORCE, `workspace_id` + 공간 복합 FK. 내보내기·복원(c0011)에 들어간다(`test_export.py`가 행 수·관계를 확인).
- 공용 문서도 revision·근거·수정안은 같은 표를 쓴다. 지원 목록·지원 검사는 `application_id IS NOT NULL`만 본다.

## 내보내기·복원 (c0011)

- `core.export_runs` — 내보내기·복원 1회 = 1행 (`kind` export/restore, c0018부터 merge, `via` cli/http, 묶음 sha256·크기·행 수·원본 수). 마지막 백업 시각 표시용. RLS FORCE, 앱 role은 **SELECT, INSERT**.
- 순환 FK 2개를 `DEFERRABLE INITIALLY IMMEDIATE`로: `fk_job_postings_confirmed_version`(공고 → 확정 버전), `fk_document_revisions_suggestion`(revision → 수정안). 평소엔 지금처럼 즉시 검사하고, 복원만 `SET CONSTRAINTS ALL DEFERRED`로 ID 그대로 넣은 뒤 commit 전에 검사한다. 추가만 되는 표를 UPDATE하지 않아도 되고, `ck_document_suggestions_applied` 같은 CHECK도 그대로 지킨다.
- 내보내기·복원은 앱 계정이 그 공간 문맥(`app.workspace_id`) 안에서만 한다. 소유자 계정·BYPASSRLS가 필요 없다. 표는 카탈로그에서 찾고(`workspace_id` 칸), 빼는 표는 `EXCLUDED_TABLES`(`jobs`, `embeddings`)에 이유와 함께 둔다. 새 표가 미룰 수 없는 순환 FK나 내보내지 않는 표로의 FK를 만들면 계획 단계에서 오류가 난다.
- 상세·형식·운영 절차: `docs/operations/backup.md`.

## 학습 검색·정리·시간대 (c0013)

`down_revision = c0012`. 모두 RLS FORCE, 공간 복합 FK.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `embeddings.target_kind` | `ck_embeddings_target_kind`에 `study_note`(학습 기록 메모), `study_mistake`(오답) 추가 | (그대로) |
| `study_items.status` | `ck_study_items_status`에 `merged` 추가 | (그대로) |
| `study_item_merges` (새 표) | 합친 항목 기록: `source_item_id` → `target_item_id`, `created_by`. 원래 항목당 1행(`uq_study_item_merges_source`), 자기 자신 금지(CHECK), 두 칸 모두 `(workspace_id, id)` 복합 FK `ON DELETE CASCADE` | `SELECT, INSERT, UPDATE, DELETE` |
| `study_requirements` | 빠진 요건 정리용 `DELETE` 권한 추가 (`study_requirement_claims`는 CASCADE, 학습 항목·시험의 `requirement_id`는 `SET NULL (칸)`) | `SELECT, INSERT, DELETE` |
| `study_settings` (새 표) | 공간당 1행(`uq_study_settings_workspace`): 표시 시간대 `timezone`(IANA 이름, 1–64자, 값 검사는 앱 `zoneinfo`), `updated_by` | `SELECT, INSERT, UPDATE` |

- 합친 관계를 `study_items`의 자기 참조 칸이 아니라 따로 둔 이유: 내보내기(O11)가 FK 순서로 표를 넣는데, 같은 표 안의 참조는 행 순서까지 맞춰야 한다. 별도 표는 `study_items` 다음에 넣으면 된다.
- 응시·경험 후보는 합쳐도 원래 항목을 가리킨 채 남는다 (`SELECT, INSERT` 표를 UPDATE하지 않는다).
- 새 표는 `workspace_id`가 있어 내보내기·복원 대상이 자동으로 된다 (`test_export.py`).
- downgrade: `study_settings`·`study_item_merges`를 지우고, `merged` 항목은 `dropped`로, 학습 임베딩은 지운 뒤 CHECK를 되돌린다.
- 상세: `docs/architecture/study.md`, `docs/architecture/search.md`.

## 감사·삭제 기록·외부 로그인·승인 즉시 무효 (c0014)

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `audit_events` | MCP 도구 호출 1회 = 1행: actor(ID·kind), `channel`(mcp-http/mcp-stdio), `event`, `tool`, `status`(ok/error), `error_code`, `duration_ms`, `args_sha256`(인자 정규 JSON의 sha256, 원문 없음), 짧은 `attributes`. 색인 `(workspace_id, created_at)` | **SELECT, INSERT** |
| `deletions` | 삭제 기록(tombstone): `target_table`(`core.sources`·`job_postings`·`applications`·`study_items`만, CHECK), `target_id`, `deleted_at`. `(workspace_id, target_table, target_id)` UNIQUE | **SELECT, INSERT** |
| `oauth_identities` | 외부 인가 서버 사용자 `(issuer, subject)` → (공간, actor). FK `(workspace_id, actor_id)` → `memberships` CASCADE, `revoked_at`. 부분 UNIQUE `(issuer, subject) WHERE revoked_at IS NULL` | `SELECT, INSERT, UPDATE` |

- 세 표 모두 RLS FORCE. `oauth_identities`는 추가로 소유자 계정 전용 SELECT 정책(`oauth_lookup`)이 있고, 앱 계정은 SECURITY DEFINER 함수 `core.lookup_oauth_identity(issuer, subject)`(살아 있는 연결의 공간·actor만 돌려줌)로만 공간 문맥 없이 찾는다. 토큰을 받는 순간에는 공간을 모르기 때문이다. 공간 ID를 토큰에서 받지 않는다.
- trigger `record_deletion` (AFTER DELETE, 위 4개 표): **그 공간 문맥의 삭제**만 남긴다 (`current_workspace_id() = OLD.workspace_id`이고 공간이 아직 있을 때). 공간 통째 삭제의 CASCADE·문맥 없는 관리 작업은 남기지 않는다. 같은 대상은 한 번 (`ON CONFLICT DO NOTHING`).
- 승인 즉시 무효 (c0019에서 `approvals_recheck` + `application_target_version`으로 바뀜, 아래): `core.invalidate_application_approvals(app, reason)`와 trigger `approvals_on_application_change`(지원 상태·공고 변경, 삭제 → `target_missing`), `approvals_on_document_change`(문서 추가·삭제, revision·제한·세는 기준·문항·소속 변경), `approvals_on_posting_change`(확정 버전·확정 값·변경 대기 → 그 공고의 지원 전부). 열린(`pending`·`approved`) 제출 승인을 `invalidated`로. 트랜잭션 로컬 설정 `career.restoring = on`이면 건너뛴다 (복원이 행을 넣는 동안).
- trigger `approvals_wake_run` (AFTER UPDATE OF status ON approvals): 열린 승인이 `invalidated`·`expired`로 바뀌고 그 승인을 기다리던 실행(`waiting_approval`, 같은 `approval_id`)이 있으면 `queued`로 바꾸고 사건(`approval_invalidated`/`approval_expired`)과 `run` job을 넣는다. 다음 단계가 실패 처리한다.
- 내보내기: `audit_events`·`deletions`는 묶음에 들어가고, `oauth_identities`는 `EXCLUDED_TABLES`(설치마다 다시 연결).
- 상세: `docs/architecture/runs.md`(무효화를 trigger로 둔 이유), `docs/architecture/mcp.md`(감사·OAuth), `docs/operations/backup.md`(삭제 기록 재적용).

## 문서·인터뷰 요청 기록과 실행 비용 장부 (c0016)

`down_revision = c0015`. 새 표는 RLS FORCE, `workspace_id` + 공간 복합 FK. 둘 다 내보내기·복원 대상이다(`test_export.py`).

| 변경 | 내용 | 앱 role |
|---|---|---|
| `model_calls.run_id`·`run_step` | 실행 안에서 부른 호출의 실행·단계. 복합 FK `(workspace_id, run_id)` → `runs` `ON DELETE SET NULL (run_id)`, `ck_model_calls_run_step`(단계 번호는 0 이상; 실행이 지워져도 단계 번호는 남는다), 부분 색인 `ix_model_calls_run (run_id) WHERE run_id IS NOT NULL` | (그대로 SELECT, INSERT) |
| `runs.cost_from_ledger` | 비용을 장부에서 다시 세는 실행인지. 기존 행은 `false`(server default), 새 실행은 `true` (`runs.md` "비용") | (그대로) |
| `document_suggestion_requests` (새 표) | 수정안 요청 1회 = 1행: `document_id`·`base_revision_id`(복합 FK CASCADE), `block_ids`·`questions`(JSONB 배열, CHECK), `proposed`·`rejected`·`dropped`(0 이상), `idempotency_key`, `run_id`(SET NULL), `model_call_id`, `created_by`. `uq_document_suggestion_requests_idempotency (document_id, idempotency_key)`, 색인 `(document_id, created_at)` | **SELECT, INSERT** |
| `interview_sessions.idempotency_key` | 세션 만들기 멱등 키. `uq_interview_sessions_idempotency (workspace_id, idempotency_key)` | (그대로) |
| `interview_question_requests` (새 표) | AI 질문 요청 1회 = 1행: `session_id`(복합 FK CASCADE), `requested`·`created`, `idempotency_key`, `run_id`(SET NULL), `model_call_id`, `created_by`. `uq_interview_question_requests_idempotency (session_id, idempotency_key)` | **SELECT, INSERT** |
| `interview_questions.request_id` | 그 질문을 만든 요청. 복합 FK `fk_interview_questions_request` CASCADE, 부분 색인 | (그대로) |

- 요청 표는 추가만 한다. 개수는 모델 응답을 검토한 뒤 정해진 값으로 한 번에 넣는다(UPDATE 없음). 요청 행과 수정안·질문 행은 같은 트랜잭션이다.
- `model_call_id`는 FK가 아닌 참고용 ID다(장부에서 그 호출을 찾을 때 쓴다).
- 실행이 지워지면 장부·요청 행은 남고 `run_id`만 NULL이 된다(비용 기록은 지우지 않는다).
- 새 순환 FK는 없다(`test_export.py`의 미루는 FK 목록 그대로).
- downgrade: 두 표와 추가한 칸·제약·색인을 지운다.

## 삭제 기록 넓히기·합치기 기록 (c0018)

`down_revision = c0016`. 새 표는 없다.

| 변경 | 내용 |
|---|---|
| `record_deletion()` | 인자 = (칸, CASCADE 부모 표) 쌍. 부모가 이미 없으면(같은 문장의 FK CASCADE로 지워진 행) 남기지 않는다. 인자가 없으면 c0014와 같다. 공간 문맥 규칙(`current_workspace_id() = OLD.workspace_id`, 공간이 있음)은 그대로 |
| trigger `record_deletion` | 12개 표에 더함: `experiences`, `claims`(← experiences), `claim_evidence`(← claims), `interview_sessions`(← experiences), `interview_questions`(← interview_sessions), `application_documents`·`application_stages`(← applications), `study_requirements`(← job_postings), `study_requirement_claims`(← study_requirements, claims), `study_assessments`, `study_sessions`(← study_items), `study_mistakes`(← study_items, study_attempts) |
| `ck_deletions_target_table` | c0014의 4개 + 위 12개 |
| `ck_export_runs_kind` | `export`, `restore`, `merge`(기존 공간에 묶음 합치기) |

- CASCADE를 `pg_trigger_depth()`로 가리지 않는 이유: RI CASCADE가 부른 자식 삭제의 AFTER trigger는 바깥 문장 끝에 깊이 1로 돌아, 직접 지운 행과 구별되지 않는다. 그래서 부모 행이 아직 있는지 본다 (부모 표는 `to_jsonb(OLD) ->> 칸`으로 읽은 ID로 찾는다).
- 인터뷰 답(`interview_answers`)은 넣지 않았다: 앱 계정에 DELETE 권한이 없고 질문·세션의 CASCADE로만 지워진다.
- 대상 표 목록은 migration의 `NEW_TABLES`와 `usecases/export.py`의 `TOMBSTONE_TABLES`(묶음 manifest `tombstone_tables`, 형식 2)가 같아야 한다. `test_deletion_log_covers_user_deleted_tables`가 trigger 인자를 DB의 CASCADE FK와 비교한다.
- downgrade: `merge` 기록은 `restore`로, 새 표의 삭제 기록 행은 지우고, trigger·CHECK·함수를 c0014로 되돌린다.
- 상세: `docs/operations/backup.md` (합치기·웹 복원·삭제 기록 재적용).

## 승인 대상 버전·OAuth 토큰·감사 보존·상태 알림 (c0019)

`down_revision = c0018`.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `core.application_target_version(app)` (새 함수) | 제출 승인의 대상 버전 sha256. `LANGUAGE sql STABLE`, SQL 표준 본문(`BEGIN ATOMIC`)이라 읽는 표·칸이 `pg_depend`에 남는다. 보이지 않는(다른 공간·없는) 지원이면 NULL. 앱의 `submission_target`과 trigger가 같이 쓴다 | EXECUTE (기본) |
| trigger `approvals_recheck` (새) | `applications`(UPDATE·DELETE), `application_documents`(INSERT·UPDATE·DELETE), `document_revisions`(INSERT), `job_postings`(UPDATE·DELETE) → `core.recheck_application_approvals(app)`: 열린 승인이 있을 때만 다시 계산, 다르면 `invalidated`(`target_changed`, 없어졌으면 `target_missing`). `career.restoring = on`이면 건너뜀 | — |
| c0014 `approvals_on_*` trigger·`invalidate_application_approvals` | 지움 (칸 목록을 따로 두지 않으려고). 올릴 때 열린 제출 승인 중 새 버전과 다른 것은 공간마다(`maintenance_workspace_ids`) `target_changed`로 닫는다 | — |
| `oauth_identities` | `UNIQUE (workspace_id, id)` 추가 (복합 FK 대상) | (그대로) |
| `oauth_tokens` (새 표) | 원격 MCP가 받은 외부 토큰: `identity_id`(복합 FK → 연결, CASCADE), `issuer`·`subject`, `token_ref`(`jti` 또는 sha256)·`ref_kind`, `via`(jwt/introspection), 처음·마지막 사용, `expires_at`, `uses`, 폐기 `revoked_at`·`revoked_by`(둘 다 있거나 없거나). `(workspace_id, issuer, token_ref)` UNIQUE. FORCE RLS | `SELECT, INSERT, UPDATE, DELETE` |
| `core.purge_audit_events(keep_days)` (새 함수) | SECURITY DEFINER, `search_path = pg_catalog`, PUBLIC 실행 권한 없음, `career_app`만 EXECUTE. 호출한 트랜잭션의 공간 문맥(`current_workspace_id()`) 안에서 `keep_days`일보다 오래된 `audit_events`만 지운다. 문맥 없으면 0, `keep_days < 7`이면 오류 | EXECUTE |
| trigger `notify_state` (새) | `runs`·`approvals` INSERT·UPDATE 문장마다 `NOTIFY career_state, '<표 이름>'`. ID 없음 (LISTEN은 권한 검사가 없다). SSE가 RLS 아래에서 다시 읽는다 | — |

- `audit_events`의 앱 권한은 그대로 `SELECT, INSERT`. 지우기는 위 함수로만 한다. 소유자가 superuser인 개발 환경에서도 함수의 WHERE가 공간·기준 시각을 다시 건다.
- 내보내기: `oauth_tokens`는 `EXCLUDED_TABLES` — 수명이 짧은 설치별 기록이고 내보내지 않는 `oauth_identities`를 FK로 가리킨다 (복원한 설치는 연결부터 다시 만든다). `audit_events`는 계속 묶음에 들어간다 (보존 기간이 지난 행은 이미 지워져 있다).
- downgrade: 새 trigger·함수·표를 지우고 c0014의 칸 비교 trigger를 되살린다. 열린 승인은 앱이 조회·사용 때 다시 확인한다.
- 상세: `docs/architecture/runs.md`("대상 버전은 한 곳에서", "실시간 갱신"), `docs/architecture/mcp.md`(OAuth 보강, 감사 보존).

## 작업 깨우기·주기 유지보수 기록 (c0020), 토큰 만료 (p0002)

`c0020_ops_postings` (down_revision `c0019`).

| 대상 | 내용 | 앱 role |
|---|---|---|
| `core.next_job_due(kinds)` | SECURITY DEFINER. 이 종류의 작업이 다음에 집힐 수 있는 **가장 이른 시각 하나**만 돌려준다: 대기(`queued`) 행의 `available_at`, 실행 중(`running`) 행의 `greatest(locked_until, available_at)` (lease 만료 = `claim_job`이 다시 집는 조건). 공간·ID·개수는 돌려주지 않는다 | EXECUTE |
| `maintenance_schedule` | 설치 전체 1행(`name = 'default'`, CHECK): `interval_seconds`, `last_started_at`, `last_finished_at`. `workspace_id` 없음 | 권한 없음 (함수로만) |
| `maintenance_begin(interval, force)` / `maintenance_finish(started_at)` / `maintenance_status()` | SECURITY DEFINER. begin은 행을 `FOR UPDATE`로 잡고 `last_started_at + interval > now()`이면 시작하지 않는다 (`force`면 무시). status는 시각·간격·다음 시각만 | EXECUTE |
| `maintenance_results` | 공간마다 1행 (`uq_maintenance_results_workspace`): 마지막으로 **무언가 정리한** 실행의 `ran_at`, 작업별 `counts`(JSON 객체), `errors`. 한 일이 없는 공간은 쓰지 않는다 (공간 수만큼 쓰지 않게) | SELECT, INSERT, UPDATE |

- `maintenance_schedule`은 RLS FORCE + 소유자 전용 정책 `maintenance_state` (`TO CURRENT_USER`, 함수 안의 `SET career.maintenance_state = on`일 때만). c0015 `maintenance_scan`과 같은 방식: 앱 역할이 설정을 켜도 정책 대상이 아니라 0행이고, 표 권한도 없다 (`test_jobs.py`).
- `maintenance_results`는 `workspace_isolation` FORCE. 기록은 그 공간 문맥(`workspace_session`)에서 쓴다.
- N개의 worker: advisory lock(동시 실행 하나) + `maintenance_begin`(간격 안에 한 번). 늦게 깬 worker는 `next_at`까지 기다렸다 다시 본다.
- 내보내기: `maintenance_results`는 `EXCLUDED_TABLES` — 이 설치의 운영 기록이고, 복원한 설치에서 다시 쌓인다. `maintenance_schedule`은 공간 범위가 없어 대상이 아니다.

`personal` 체인 `p0002_token_expiry` (`apps/personal-api`, down_revision `p0001`): `personal.api_tokens`에 `expires_at`(CHECK `expires_at > created_at`), `last_used_at`. `personal.lookup_api_token(hash)`가 폐기·**만료** 토큰을 빼고 `token_id`·`last_used_at`도 돌려준다. 마지막 사용 시각은 인증 때 5분에 한 번만 쓴다 (그 공간 문맥, `last_used_at < now() - 5분`일 때만). 토큰은 여전히 내보내기 대상 밖(`personal.*`).

## 공간 공용 설정 (c0021)

`down_revision = c0020` (병렬 작업 때 c0017로 만들었다가 합칠 때 마지막으로 옮겼다. c0017 번호는 비어 있다). 표시 시간대를 학습 전용(`study_settings`)에서 공간 공용 설정으로 옮기고, 기술명 별칭 사전을 더한다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `workspace_settings` (새 표) | 공간당 1행(`uq_workspace_settings_workspace`). 표시 시간대 `timezone`(IANA 이름, NULL = 기본 `Asia/Seoul`, 1–64자, 값 검사는 앱 `zoneinfo`), 기술명 별칭 `tech_aliases` JSONB 배열 `[{term, aliases}]`(`ck_workspace_settings_tech_aliases`: 배열, 200개 이하, 항목 검사는 앱 `parse_alias_entries`), `updated_by` | `SELECT, INSERT, UPDATE` |
| `study_settings` | 지움. 값은 `workspace_settings.timezone`으로 옮긴다 | – |

- 옮기기: upgrade는 모든 공간의 행을 한 번에 복사해야 해서 그동안만 `study_settings`의 `FORCE ROW LEVEL SECURITY`를 끈다(`NO FORCE`, 같은 트랜잭션에서 표를 지움). 소유자가 superuser가 아닌 설치에서도 복사가 빠지지 않는다. downgrade는 시간대가 있는 행만 `study_settings`로 되돌린다 (별칭은 버린다).
- RLS FORCE, `workspace_id` + 공간 복합 키. 내보내기·복원 대상이 자동으로 된다 (`test_export.py`).
- API: `GET/PUT /core/workspace-settings` (`settings.read` = read, `settings.write` = draft-write). 준 칸만 바꾼다. `GET/PUT /core/study/settings`는 같은 시간대를 읽고 쓴다.
- 키워드 검색용 SECURITY DEFINER 함수(trigram 색인 사용)는 시험했지만 넣지 않았다 (2글자 한국어 낱말은 색인 이득이 없고, 소유자가 superuser가 아니면 RLS가 그대로 적용된다. `search.md` "RLS 아래 trigram 색인").
- 상세: `docs/architecture/study.md`(시간대·별칭), `docs/architecture/applications.md`(문서 검사의 별칭), `docs/architecture/models.md`(판정 모델).

## 판정 응답 캐시 (c0022)

`c0022_search_judges` (`down_revision = c0021`). 판정 모델(`judge_claim`·`judge_visibility`) 응답을 공간별로 담아 같은 질문을 다시 부르지 않는다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `judge_cache` (새 표) | `workspace_id`(FK CASCADE) + 공간 복합 키, `purpose`(CHECK `judge_claim`·`judge_visibility`), `model`(`엔드포인트/모델`), `prompt_version`, `input_hash`(CHECK 소문자 hex 64자), `response_text`(CHECK 65,536자 이하), 원래 호출의 `endpoint`·`result_model`·`cost_usd`·`cost_basis`·`call_id`, `hits`(CHECK ≥ 0), `created_at`·`last_hit_at`·`expires_at`. UNIQUE `uq_judge_cache_key`(공간, purpose, model, prompt_version, input_hash), 색인 `ix_judge_cache_expires` | `SELECT, INSERT, UPDATE, DELETE` |

- FORCE RLS(`rls.workspace_isolation_sql`). 다른 공간 문맥에서는 0행 (시험: `pg_class.relforcerowsecurity`, 공간 둘의 같은 질문).
- 쓰기는 `INSERT … ON CONFLICT (uq_judge_cache_key) DO UPDATE` 하나. 꺼낼 때는 그 행을 `FOR UPDATE`로 잡고 `hits`를 올린다.
- 정리: 쓸 때 그 공간의 만료 행을 지운다(`CAREER_JUDGE_CACHE_DAYS`, 기본 30일). `DELETE /core/judges/cache`로 비운다. 공간을 지우면 CASCADE.
- 내보내기: `EXCLUDED_TABLES` — 다시 물으면 생기는 파생 자료이고, 응답 인용(`quote`)에 문서 글이 들어 있어 지운 문서의 글을 백업으로 옮기지 않는다 (`test_export.py`).
- 같은 migration 번호에 검색 쪽 표 변경은 없다: 별칭 검색어 넓히기는 c0021 `workspace_settings.tech_aliases`를 읽기만 하고, 2글자 bigram 색인은 보류했다 (`search.md`).
- 상세: `docs/architecture/models.md` "응답 캐시".

## 작업 lease 토큰 (c0023)

`c0023_documents_runs_web` (`down_revision = c0022`). 웹에서 시작하는 일괄 실행·DOCX 원본·PDF 숨김 글 검사 보강과 같이 들어갔지만 표 변경은 lease 하나다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `jobs.lease_token` | uuid, NULL 허용. `claim_job`이 집을 때마다 `gen_random_uuid()`로 새로 쓴다 | 기존 권한 그대로 |
| `core.claim_job(...)` | 다시 만든다(DROP + CREATE, 돌려주는 열이 바뀌어서). 돌려주는 행에 `lease_token`을 더한다. 나머지(집는 조건·잠금·SECURITY DEFINER)는 같다 | EXECUTE (다시 GRANT) |

- 용도: 실행 단계 저장·heartbeat·끝 기록이 "같은 `attempts` + 같은 `lease_token` + `locked_until > now()`"일 때만 쓰게 한다 (`runs.md` "재시작 후 재개와 멱등"). `attempts`는 대기 때 0으로 돌아가므로 혼자서는 주인을 가리지 못한다.
- `jobs`는 이전처럼 `allow_unforced`(공간 RLS는 켜져 있고 FORCE는 아님, 집기는 함수로만). 새 열은 공간 데이터가 아니라 내보내기 대상 규칙도 그대로다 (`test_export.py` 통과).
- downgrade: 옛 `claim_job`을 되살리고 열을 지운다.
- 원본 MIME에 DOCX를 더한 것(`uploads.ALLOWED_MIME_TYPES`)과 인터뷰 질문의 `run_id`(이미 있던 `interview_question_requests.run_id`를 outer join)는 migration이 필요 없다.

## 웹 복원 작업 상태·옛 head 묶음 변환 (c0024)

`down_revision = c0023` (c0022·c0023·c0025와 병렬로 만들어 합칠 때 번호 순서로 다시 이었다). 웹 복원 작업 상태를 프로세스 메모리에서 DB로 옮긴다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `restore_jobs` (새 표) | 웹 복원 작업 하나 = 올린 묶음 하나, 공간당 1행(`uq_restore_jobs_workspace`: 새 업로드가 옛 작업을 바꾼다). 상태 `status`(`ck_restore_jobs_status`: uploaded·verifying·verified·applying·done·failed), 단계·진행 수(`phase`·`done`·`total`), 파일 크기·sha256(`ck_restore_jobs_sha256`)·암호화 여부·표시용 이름, 삭제 기록 줄 수, 확인한 겹침 정책(`ck_restore_jobs_on_duplicate`), 확인·합치기 보고 `report`(JSONB 객체), 오류 코드·설명, 실행 중일 때 돌리는 프로세스 표시 `runner`와 생존 신호 `heartbeat_at` | `SELECT, INSERT, UPDATE, DELETE` |

- 올린 파일 경로는 두지 않는다. 공유 폴더(`CAREER_RESTORE_DIR`) 안 `career-restore-upload-<작업 ID>`로 정해진다. age 신원은 저장하지 않는다.
- 실행 중 작업은 돌리는 프로세스가 2초마다 진행과 `heartbeat_at`을 쓴다. 60초 넘게 신호가 없는 실행 중 작업은 읽는 쪽이 `failed`/`interrupted`로 닫는다 (재시작·죽은 프로세스). 상태 전이(확인·합치기 시작)는 `WHERE status = 이전 상태` 조건부 UPDATE 하나라서 두 프로세스가 같은 작업을 같이 시작하지 못한다.
- RLS FORCE (`workspace_isolation`). 내보내기에서는 뺀다 (`EXCLUDED_TABLES`: 이 설치의 임시 파일을 가리키는 일시 상태). 묶음 변환 단계는 `no_data_change("c0024")`.
- 같은 백로그 묶음의 코드 변경(migration 없음): 옛 core migration head 묶음 변환 `SCHEMA_STEPS`(c0019·c0020·c0024 바뀜 없음, c0021 `study_settings` → `workspace_settings`), 합치기 내용 키(`EXTRA_MERGE_KEYS`의 `SameExcept`), 합친 뒤 열린 승인 다시 확인(c0019 `recheck_application_approvals`). 상세 `docs/operations/backup.md`.
- **새 migration 규칙**: 내보내는 표의 표·칸·타입을 바꾸는 migration은 `usecases/export.py`에 `@schema_step("cNNNN", …)`을, 바꾸지 않으면 `no_data_change("cNNNN", …)`을 같이 넣는다. `test_schema_steps_cover_every_migration`이 migration을 하나씩 올리며 확인한다. 합칠 새 표는 `EXTRA_MERGE_KEYS` 또는 `CONTENT_ONLY_TABLES`에 분류한다 (`test_merge_keys_cover_keyless_tables`).

## 스캔 PDF 비전 비용 확인 (c0025)

`c0025_mcp_ops_postings` (`down_revision = c0024`). 같은 작업 묶음의 나머지(JWT introspection·캐시 시간, SSE 공간별 묶어 읽기, 토큰 이름·만료 연장, 그리팅 선택형 문항, 모르는 봇 확인 화면)는 스키마를 바꾸지 않는다 — 선택형 문항은 기존 `job_postings.fields`·`job_posting_versions.extracted` JSON의 새 키 `application_questions`, 토큰 변경은 `personal.api_tokens`의 기존 칸(`label`·`expires_at`, 앱 role에 UPDATE가 이미 있어 `p0003` 불필요).

| 변경 | 내용 | 앱 role |
|---|---|---|
| `job_fetch_runs.status` | CHECK에 `needs_confirmation` 추가 (`ck_job_fetch_runs_status`) | 그대로 |
| `job_fetch_confirmations` (새 표) | 확인 요청 한 건: `posting_id`·`fetch_run_id`(실행당 하나, `uq_job_fetch_confirmations_run`)·`used_run_id`(확인을 쓴 실행, SET NULL), `status` (`pending`·`confirmed`·`declined`·`used`·`superseded`), `content_digest`(`pdf:<sha256>`, 원본 바이트 해시만), `pages`(읽을 쪽 1부터, 64개 이하), `pages_total`·`images`·`input_tokens`·`output_tokens`(추정), `estimated_usd`·`max_usd`(단가를 모르면 NULL)·`threshold_usd`, `endpoint`·`model`, `decided_by`·`decided_at` (CHECK: 결정한 상태만 `decided_at`이 있다) | `SELECT, INSERT, UPDATE` |

- RLS FORCE (`workspace_isolation`), 공간 복합 FK(`posting_id`·`fetch_run_id` CASCADE). 지우기 권한은 없다: 공고를 지울 때(미확정 초안 정리 포함) CASCADE로만 사라진다.
- 공간을 넘는 조회가 없어 SECURITY DEFINER 함수는 없다.
- 내보내기·복원 대상이 자동으로 된다 (`test_export.py`). 원문 토큰·키·PDF 바이트는 들어 있지 않다.
- downgrade: 표를 지우고 `needs_confirmation` 실행을 `failed`/`needs_manual_check`로 돌린 뒤 CHECK를 되돌린다 (FORCE RLS는 소유자에게도 걸리므로 그동안만 `NO FORCE`, c0021과 같은 방식).
- 흐름·규칙: `postings.md` "스캔 PDF 쪽 고르기·비용 확인".

## 판정 캐시 대상 ID (c0026)

`c0026_judge_cache_targets` (`down_revision = c0025`). 판정 응답 캐시(c0022)에 응답을 만든 요청의 대상을 적고, 대상을 지우면 응답도 지운다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `judge_cache.claim_ids` · `document_ids` | uuid[], NOT NULL, 기본 `'{}'`. 주장 판정에 보낸 후보 주장 / 가시성 판정에 합친 지원 문서 | 기존 권한 그대로 |
| `judge_cache_forget()` | trigger 함수 (plpgsql, SECURITY INVOKER, `search_path = pg_catalog`). 지운 행의 전이 표 `gone`을 공간별로 모아 `workspace_id`가 같고 배열이 겹치는(`&&`) 캐시 행을 지운다 | – |
| trigger `judge_cache_forget` | `AFTER DELETE … REFERENCING OLD TABLE AS gone FOR EACH STATEMENT`, `core.claims`·`core.application_documents` 두 표 | – |

- 문장 단위라 경험·지원을 지울 때의 CASCADE나 복원의 삭제 기록 재적용처럼 많이 지워도 캐시 DELETE는 문장마다 한 번이다.
- FORCE RLS 그대로: 지우는 세션의 공간 문맥 아래에서 돈다. 공간 문맥 없이 지운 관리 작업은 캐시 행을 보지 못해 남기고(만료·비우기로 정리), 공간을 지우면 c0022의 CASCADE.
- c0026 전의 행은 빈 배열이다 (대상을 알 수 없음). 만료(기본 30일)나 `DELETE /core/judges/cache`로만 지워진다.
- 내보내지 않는 표라 묶음 변환은 `no_data_change("c0026")`. downgrade: trigger·함수·칸을 지운다.
- 시험: `test_korean_search_judges_more.py` (주장·경험·지원 문서·지원 삭제, 다른 공간 그대로, trigger 위치·설정).

## 웹 복원을 worker 큐로·비슷한 행 선택 (c0027)

`c0027_restore_worker` (`down_revision = c0026`. 병렬로 만들어 합칠 때 번호 순서로 이었다). 웹 복원의 확인·합치기를 API 프로세스의 백그라운드 작업에서 worker 작업 큐(`core.jobs`, kind `restore`, 대상 = `restore_jobs.id`)로 옮긴다. `core.jobs.kind`에는 CHECK가 없어 큐 쪽 변경은 없다.

| 변경 | 내용 | 앱 role |
|---|---|---|
| `restore_jobs.started_by` | 확인·합치기를 시작한 사람 (시작할 때마다 고친다, 올린 사람 `actor_id`는 그대로). worker가 이 사람으로 합치고, 실행 때 이 사람의 지금 역할을 다시 본다. NULL(옛 행)이면 `actor_id` | 그대로 |
| `restore_jobs.near_choices` | 합치기에서 고른 비슷한 행 쌍 `[{table, incoming_id, existing_id}]` (들어오는 행을 건너뛰고 기존 행으로 잇는 쌍만, `ck_restore_jobs_near_choices`: 배열·1000개 이하). 없으면 기본(둘 다 둠) | 그대로 |

- 큐에서 기다리는 작업(`phase = queued`)은 생존 신호가 오래돼도 끊김이 아니다. 끊김 판정은 큐 작업이 없거나 끝났을 때, 또는 worker가 집은 뒤 lease도 지났을 때만. 끊김으로 닫은 작업은 worker가 나중에 집어도 돌지 않는다 (`runner`·`status` 확인).
- 신호 간격·끊김 판정 시간은 설정 (`CAREER_RESTORE_HEARTBEAT_SECONDS` 기본 2, `CAREER_RESTORE_STALE_SECONDS` 기본 60, 판정 ≥ 간격×3).
- `restore_jobs`는 내보내지 않는 표라 묶음 변환 단계는 `no_data_change("c0027")`.
- 같은 묶음의 코드 변경(migration 없음): 묶음 변환 하한 `SCHEMA_FLOOR` c0018 → c0011 (c0012·c0013·c0014·c0016 단계, c0015·c0018 바뀜 없음), 칸을 그 server default와 함께 더하는 도우미(`read_column_defaults`, `_Archive.add_columns`), 합치기 비슷한 행 보고·선택(`SameExcept.identity`). 상세 `docs/operations/backup.md`.

## 원본 파싱의 숨김 서식 글 빼기 (c0029)

`c0029_docx_hidden_runs` (`down_revision = c0027`; 병렬로 만들어 합칠 때 번호 순서로 이었다).

| 변경 | 내용 | 앱 role |
|---|---|---|
| `parse_runs.hidden_text` | text NOT NULL DEFAULT `'keep'`, CHECK `ck_parse_runs_hidden_text` (`keep`·`exclude`). 그 파싱 실행이 숨김 서식 글(DOCX `w:vanish`·`specVanish`·`webHidden`)을 블록에 넣었는지(`keep`) 뺐는지(`exclude`) | 기존 권한 그대로 |

- 업로드 때 정한다 (`POST /core/sources` 폼 `hidden_text`). 지금은 DOCX만 `exclude`를 따르고, 다른 형식은 `keep`으로 저장한다. 같은 파일 중복 업로드는 처음 실행의 값을 돌려준다.
- 기존 행은 `keep`(지금까지의 동작). RLS·FORCE·FK는 그대로 (칸 하나).
- 내보내기: `parse_runs`는 내보내는 표라 `@schema_step("c0029")`가 옛 묶음 행에 `hidden_text = 'keep'`을 넣는다 (`test_schema_steps_cover_every_migration`).
- downgrade: CHECK와 칸을 지운다.
- 규칙: `applications.md` "DOCX 원본" — 숨김 run 빼기.

## 경험 구조 (c0031, O19)

`c0031` (`down_revision = c0030`). 브레인 색인·연표·표·거름의 바탕 (plan §20).

| 변경 | 내용 | 앱 role |
|---|---|---|
| `experiences.kind` | text NOT NULL DEFAULT `'other'`, CHECK `ck_experiences_kind` (`work`·`project`·`education`·`activity`·`other`) | 기존 권한 그대로 |
| `experiences.parent_id` | uuid NULL. FK `fk_experiences_parent` (`workspace_id, parent_id`) → `experiences (workspace_id, id)`: 같은 공간의 경험만. `ON DELETE SET NULL (parent_id)`(부모를 지우면 자식은 맨 위로), 같은 표를 가리키므로 `DEFERRABLE INITIALLY IMMEDIATE`(c0012처럼 복원 때만 트랜잭션 끝에서 검사). CHECK `ck_experiences_parent_self`. 색인 `ix_experiences_parent`(부분) | 그대로 |
| trigger `experience_one_level` | `BEFORE INSERT OR UPDATE OF parent_id`, plpgsql(`search_path = pg_catalog`, SECURITY INVOKER — 공간 RLS 아래에서 돈다). 한 단계만: 부모가 자식이면 `experience_parent_is_child`, 자식이 있는 경험을 자식으로 두면 `experience_has_children` (check_violation). usecase가 먼저 같은 규칙으로 막고(409), trigger는 동시 수정의 안전장치 | – |
| `experiences.started_on`·`ended_on` | date NULL, 그 달 1일 (`ck_experiences_months`). `ck_experiences_period`: 끝 ≥ 시작, 끝이 있으면 시작도, 진행 중이면 끝 없음 | 그대로 |
| `experiences.ongoing` | bool NOT NULL DEFAULT false | 그대로 |
| `experiences.tags` | text[] NOT NULL DEFAULT `'{}'`, `ck_experiences_tags` (30개 이하, NULL 원소 없음). 정규화(앞뒤·겹친 공백, 대소문자 무시 중복 빼기, 40자)는 usecase | 그대로 |

- 채우기: 기존 행의 `period_text`에서 시작·끝 달이 분명할 때만 날짜를 채운다 (`domain.experiences.parse_period`: `2023.03 – 2024.08`, `2023-03 ~ 2024-08`, `2023년 3월 - 2024년 8월`, `2024.10 – 현재`(진행 중)). 연도만·두 자리 연도·달 하나·거꾸로·일까지·다른 글이 섞인 기간은 비운다. `period_text`는 그대로. 공간 문맥 없이 모든 행을 고치려고 채우는 동안만 NO FORCE (c0021과 같은 방식), 끝나면 FORCE.
- 추출: 모델이 경험마다 `kind`·`tags`를 후보로 낸다. 날짜는 모델 값이 아니라, 모델이 옮긴 `period` 글이 보낸 블록에 그대로 있을 때 같은 `parse_period`로만 읽는다. 태그는 블록에 (대소문자 무시) 나오는 낱말만 10개까지. 모르는 종류는 `other`.
- 내보내기: `@schema_step("c0031")`가 옛 묶음의 경험 행에 `kind = 'other'`, `parent_id = null`, `tags = []`, 날짜는 migration과 같은 규칙으로 채운다. 합치기 내용 키(`same_except`)는 새 칸을 "정리하며 바뀌는 칸"으로 뺀다 (정체 칸은 제목·조직·역할·기간 원문 그대로).
- downgrade: trigger·함수·색인·FK·CHECK·칸을 지운다.
- 시험: `packages/core/tests/test_experience_structure.py` (기간 읽기 표, 채우기·downgrade, 한 단계, 다른 공간 부모, 거름·정렬, 300건 쪽, 색인 수, 추출 후보, HTTP 코드).


## 회사 조사 (c0033, O13)

`c0033` (`down_revision = c0031`; c0032와 함께 들어오면 합칠 때 다시 잇는다). 회사의 공개 정보를 원문 인용과 함께 둔다 (plan §19.1). **참고 지식**이다: 사용자의 경험이 아니고, 문서 근거(`document_claims`는 `claims`만 가리킨다)로 쓸 수 없다.

| 테이블 | 내용 | 앱 role |
|---|---|---|
| `companies` | 이름·`name_key`(NFKC·대소문자 무시, `(주)`·`주식회사`·`Inc.` 같은 법인 표시와 기호 뺌)·별칭(`text[]`, 20개 이하)·홈페이지·선택 식별자(`corp_code` 숫자 8자리, `business_no` 숫자 10자리). `UNIQUE (workspace_id, name_key)` | 전체 |
| `company_pages` | 인용의 원문 한 건. `origin`: `url`(공고와 같은 fetch 경로, worker job `company_fetch`)·`paste`. 상태 `pending`/`done`/`failed`, 오류 코드, 글(200,000자 이하), 가져온 시각, 모델 호출, 후보 수, 메모. `done`이면 글·가져온 시각 필수. 회사를 지우면 함께 지워진다 | 전체 |
| `company_facts` | 항목 하나: 종류(`talent`·`values`·`business`·`product`·`finance`·`culture`·`news`·`hiring`·`other`)·본문·인용(`quote`, 원문 안 위치 `quote_start`/`quote_end`)·원문 주소·가져온 시각(NOT NULL)·기준일(`as_of` + `as_of_precision` year/month/day, 함께 null)·상태(`candidate`/`confirmed`)·출처(`model`/`user`). `page_id` NOT NULL — 원문 없는 항목은 없다 | 전체 |
| `job_postings.company_id` | uuid NULL. FK (`workspace_id, company_id`) → `companies`, `ON DELETE SET NULL (company_id)` (회사를 지워도 공고는 남는다). 부분 색인 | 기존 권한 그대로 |

- 규칙 (usecase `career_core.usecases.companies`, DB CHECK가 한 번 더): 인용은 원문 글에 있어야 저장된다(공백 차이만 허용, `quote_not_found`). 재무 항목은 기준일이 있어야 한다(`ck_company_facts_finance_as_of`, HTTP `finance_needs_as_of`). 기준일은 추측하지 않는다 — 모델 후보의 기준일은 연도(와 달·날)가 인용이나 `as_of_quote`에 그대로 있을 때만 남기고, 재무 후보는 그렇지 않으면 버린다.
- 오래됨: 기준일 기간의 끝(없으면 가져온 시각)이 오늘보다 365일 넘게 지났으면 보기에서 `stale: true`.
- 후보: 모델(용도 `company_facts`, 없으면 `default`, 자료 등급 `public`)이 고른 항목은 `candidate`로 저장되고 사람(`actor.is_human`)만 확인한다. 사람이 직접 고른 인용은 바로 `confirmed`. 모델이 없으면 원문 글만 저장한다 (메모 `no_model`).
- 공고 묶기: 연결되지 않은 공고 중 회사 이름의 `name_key`가 회사 이름·별칭과 같은 것을 제안만 한다. 연결은 사람이 `link-postings`로.
- 내보내기: 세 표는 카탈로그로 자동 포함. `@schema_step("c0033")`가 옛 묶음에 빈 표와 `job_postings.company_id = null`을 넣는다. 대기 중인 `company_fetch`는 `pending_work`로 다시 큐에 들어간다. 삭제 기록(tombstone) 대상은 아니다.
- 시험: `packages/core/tests/test_companies.py` (이름 키·기준일, 인용 필수·DB CHECK, HTTP 오류·권한, URL 가져오기·후보 검증, 붙여넣기+모델, 공고 묶기·다른 공간 0건, 문서 근거 거부, MCP 도구, 내보내기 왕복).
