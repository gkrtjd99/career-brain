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

