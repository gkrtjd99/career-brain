# DB·공간 격리·migration (O02)

## 소유 영역 (schema)

| schema | 소유 | 내용 |
|---|---|---|
| `core` | career-brain `packages/core` | workspaces, actors, memberships, 이후 모든 사용자 자료 |
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
5. 예외: `personal.api_tokens`는 hash 조회 함수(`personal.lookup_api_token`, SECURITY DEFINER)를
   위해 FORCE를 켜지 않는다. 앱 역할의 직접 조회는 여전히 RLS로 막힌다.

회귀 시험: `workspace_isolation_problems()`가 새 테이블의 RLS 누락을 잡는다.

## migration 실행

`career_core.db.migrate.upgrade(url, chains)` 하나가 `pg_advisory_lock`을 잡고 core → (personal | platform)
순서로 실행한다. API 시작 시 자동 실행하지 않는다.

```bash
uv run personal-api migrate
```

운영에서 소유자가 superuser가 아니면 `FORCE RLS` 때문에 데이터 migration도 공간 문맥이 필요하다.
