"""core: 작업 lease 값 — lease를 잃은 worker의 늦은 저장 막기

Revision ID: c0023
Revises: c0022

- ``core.jobs.lease_token``: ``claim_job``이 집을 때마다 새로 정하는 값(``gen_random_uuid()``).
  worker는 처리하는 동안 이 값을 들고 있고, 실행 단계의 위치·결과를 저장하는 트랜잭션이 작업 행을 잠가
  "같은 ``attempts``·같은 ``lease_token``·안 지난 ``locked_until``"인지 확인한다 (``jobs.lease``).
  ``attempts``만으로는 부족하다: 외부 한도 대기(``retry_after``)는 ``attempts``를 0으로 되돌리므로 lease를
  잃은 옛 worker와 나중에 다시 집은 worker의 ``attempts``가 같아질 수 있다.
- ``claim_job``은 돌려주는 칸이 하나 늘어 지우고 다시 만든다 (SECURITY DEFINER, 공간 문맥 없음, 권한 그대로).
  ``jobs``는 c0002대로 FORCE 없는 RLS다 (``claim_job``이 소유자 권한으로 공간 문맥 없이 집는다).
- 이미 ``running``인 작업은 ``lease_token``이 NULL이다. 그 작업을 쥔 옛 worker는 값이 없으므로
  ``attempts``만 비교한다 (``Lease.token is None``). 다시 집히면 값이 생긴다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0023"
down_revision = "c0022"
branch_labels = None
depends_on = None

S = "core"

_SIGNATURE = f'"{S}".claim_job(text[], integer)'

_PICK = """
   WHERE j.id = (
     SELECT c.id FROM core.jobs c
      WHERE c.kind = ANY (p_kinds)
        AND c.available_at <= now()
        AND (c.status = 'queued' OR (c.status = 'running' AND c.locked_until < now()))
      ORDER BY c.available_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1)
"""


def _grant() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {rls.APP_ROLE}")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_token", sa.Uuid()), schema=S)
    op.execute(f"DROP FUNCTION {_SIGNATURE}")
    op.execute(
        f"""
CREATE FUNCTION "{S}".claim_job(p_kinds text[], p_lease_seconds integer)
RETURNS TABLE (job_id uuid, workspace_id uuid, kind text, target_id uuid, attempts integer,
               lease_token uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  UPDATE core.jobs j
     SET status = 'running',
         attempts = j.attempts + 1,
         locked_until = now() + make_interval(secs => p_lease_seconds),
         lease_token = gen_random_uuid()
{_PICK}
  RETURNING j.id, j.workspace_id, j.kind, j.target_id, j.attempts, j.lease_token
$$;
"""
    )
    _grant()


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {_SIGNATURE}")
    op.execute(
        f"""
CREATE FUNCTION "{S}".claim_job(p_kinds text[], p_lease_seconds integer)
RETURNS TABLE (job_id uuid, workspace_id uuid, kind text, target_id uuid, attempts integer)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  UPDATE core.jobs j
     SET status = 'running',
         attempts = j.attempts + 1,
         locked_until = now() + make_interval(secs => p_lease_seconds)
{_PICK}
  RETURNING j.id, j.workspace_id, j.kind, j.target_id, j.attempts
$$;
"""
    )
    _grant()
    op.drop_column("jobs", "lease_token", schema=S)
