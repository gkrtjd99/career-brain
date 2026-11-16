"""RLS·권한 SQL 도우미. migration에서 ``op.execute``로 사용한다.

- 런타임 앱은 ``career_app`` 역할의 멤버인 로그인 계정으로 접속한다
  (superuser·BYPASSRLS·테이블 소유자 아님).
- 공간 문맥은 트랜잭션 로컬 설정 ``app.workspace_id``로 전달한다. 설정이 없으면 0행.
"""

from __future__ import annotations

APP_ROLE = "career_app"
WORKSPACE_SETTING = "app.workspace_id"


def ensure_app_role_sql() -> str:
    return f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
    CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;
"""


def current_workspace_function_sql(schema: str = "core") -> str:
    return f"""
CREATE FUNCTION "{schema}".current_workspace_id() RETURNS uuid
LANGUAGE sql STABLE
AS $$ SELECT nullif(current_setting('{WORKSPACE_SETTING}', true), '')::uuid $$;
"""


def workspace_isolation_sql(
    schema: str, table: str, *, column: str = "workspace_id", force: bool = True
) -> list[str]:
    """``force=False``는 소유자(SECURITY DEFINER 함수)의 좁은 조회가 필요한 신원 테이블에만 쓴다."""
    q = f'"{schema}"."{table}"'
    cond = f"{column} = core.current_workspace_id()"
    stmts = [f"ALTER TABLE {q} ENABLE ROW LEVEL SECURITY"]
    if force:
        stmts.append(f"ALTER TABLE {q} FORCE ROW LEVEL SECURITY")
    stmts.append(f"CREATE POLICY workspace_isolation ON {q} USING ({cond}) WITH CHECK ({cond})")
    return stmts


def grant_app_sql(
    schema: str, table: str, privileges: str = "SELECT, INSERT, UPDATE, DELETE"
) -> str:
    return f'GRANT {privileges} ON "{schema}"."{table}" TO {APP_ROLE}'
