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
| `experiences` | 경험 묶음(제목·조직·역할·기간 원문). `origin`: `ai_extraction`/`interview`/`user` | 전체 |
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

- `core.job_postings` — 공간마다 `canonical_url` UNIQUE (정규화: 소문자 호스트, 추적 파라미터·조각·기본 포트·끝 슬래시 제거, 쿼리 정렬). `status` draft/saved/archived. 확정한 값은 `fields`, 항목별 근거는 `evidence`(structured 경로·quote 위치·user). `confirmed_version_id`, `pending_changes`(확정 후 바뀐 항목), saved면 `saved_by`·`saved_at` 필수 (`ck_job_postings_saved`).
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

## 내보내기·복원 (c0011)

- `core.export_runs` — 내보내기·복원 1회 = 1행 (`kind` export/restore, `via` cli/http, 묶음 sha256·크기·행 수·원본 수). 마지막 백업 시각 표시용. RLS FORCE, 앱 role은 **SELECT, INSERT**.
- 순환 FK 2개를 `DEFERRABLE INITIALLY IMMEDIATE`로: `fk_job_postings_confirmed_version`(공고 → 확정 버전), `fk_document_revisions_suggestion`(revision → 수정안). 평소엔 지금처럼 즉시 검사하고, 복원만 `SET CONSTRAINTS ALL DEFERRED`로 ID 그대로 넣은 뒤 commit 전에 검사한다. 추가만 되는 표를 UPDATE하지 않아도 되고, `ck_document_suggestions_applied` 같은 CHECK도 그대로 지킨다.
- 내보내기·복원은 앱 계정이 그 공간 문맥(`app.workspace_id`) 안에서만 한다. 소유자 계정·BYPASSRLS가 필요 없다. 표는 카탈로그에서 찾고(`workspace_id` 칸), 빼는 표는 `EXCLUDED_TABLES`(`jobs`, `embeddings`)에 이유와 함께 둔다. 새 표가 미룰 수 없는 순환 FK나 내보내지 않는 표로의 FK를 만들면 계획 단계에서 오류가 난다.
- 상세·형식·운영 절차: `docs/operations/backup.md`.
