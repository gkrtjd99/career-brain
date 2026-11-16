"""core: 다음 작업 예정 시각, 주기 유지보수 마지막 실행 기록 (운영 백로그)

Revision ID: c0020
Revises: c0019

- ``core.next_job_due(kinds)``: worker가 집을 수 있는 가장 이른 시각 하나만 돌려준다.
  대기(queued) 작업의 ``available_at``, lease가 끝날 running 작업의 ``locked_until``(과 그 작업의
  ``available_at`` 중 늦은 쪽, ``claim_job`` 조건과 같다) 중 가장 이른 것. 공간·대상 ID·종류별 수는
  돌려주지 않는다 (``claim_job``처럼 공간 문맥 없이 부르므로 시각 하나 외에는 새지 않게).
  NOTIFY가 없는 경우(죽은 worker의 lease 만료, worker 시작 전에 예약된 미래 작업)를 fallback 확인
  간격보다 일찍 집으려고 worker가 쉬기 전에 부른다.
- ``core.maintenance_schedule``: 설치 전체에 한 행(``name = 'default'``). 주기 유지보수가 마지막으로
  시작·끝난 시각과 간격. worker가 N개여도 간격마다 한 번만 돈다 (``maintenance_begin``이 행을 잠그고
  간격 안이면 거절). 공간 범위가 없으므로 FORCE RLS + 함수 안에서만 켜지는 설정을 보는 소유자 전용
  정책(c0015 ``maintenance_scan``과 같은 방식)이고, 앱 계정은 SECURITY DEFINER 함수 3개로만 다룬다.
  함수는 시각·간격만 돌려준다 (공간 수·처리 수는 공간 사이에 새지 않게 이 표에 두지 않는다).
- ``core.maintenance_results``: 공간마다 무언가 정리한 마지막 자동 유지보수 결과 1행(작업별 처리
  수·오류 수). 한 일이 없는 공간은 쓰지 않는다 (실행마다 공간 수만큼 쓰지 않게).
  일반 공간 표(FORCE RLS, ``workspace_isolation``). 내보내기에서는 뺀다 (설치의 운영 기록:
  ``usecases/export.py`` ``EXCLUDED_TABLES``).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0020"
down_revision = "c0019"
branch_labels = None
depends_on = None

S = "core"
STATE_SETTING = "career.maintenance_state"


def upgrade() -> None:
    # ------------------------------------------------------------ 다음 작업 예정 시각
    op.execute(
        f"""
CREATE FUNCTION "{S}".next_job_due(p_kinds text[]) RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT min(due) FROM (
    SELECT min(j.available_at) AS due FROM core.jobs j
     WHERE j.status = 'queued' AND j.kind = ANY (p_kinds)
    UNION ALL
    SELECT min(greatest(j.locked_until, j.available_at)) FROM core.jobs j
     WHERE j.status = 'running' AND j.kind = ANY (p_kinds) AND j.locked_until IS NOT NULL
  ) d
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".next_job_due(text[]) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".next_job_due(text[]) TO {rls.APP_ROLE}')

    # ------------------------------------------------------------ 유지보수 일정 (설치 전체 1행)
    op.create_table(
        "maintenance_schedule",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("interval_seconds", sa.Float()),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("name = 'default'", name="ck_maintenance_schedule_name"),
        sa.CheckConstraint(
            "interval_seconds is null or interval_seconds > 0",
            name="ck_maintenance_schedule_interval",
        ),
        schema=S,
    )
    op.execute(f'ALTER TABLE "{S}".maintenance_schedule ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{S}".maintenance_schedule FORCE ROW LEVEL SECURITY')
    # 앱 계정에는 권한도 정책도 없다. 소유자도 함수의 SET 안에서만 행을 본다.
    op.execute(
        f"""
CREATE POLICY maintenance_state ON "{S}".maintenance_schedule FOR ALL TO CURRENT_USER
USING (current_setting('{STATE_SETTING}', true) = 'on')
WITH CHECK (current_setting('{STATE_SETTING}', true) = 'on');
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".maintenance_begin(p_interval_seconds double precision, p_force boolean)
RETURNS TABLE (started boolean, started_at timestamptz, next_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
SET "{STATE_SETTING}" = 'on'
AS $$
DECLARE
  r core.maintenance_schedule%ROWTYPE;
  iv double precision := CASE WHEN p_interval_seconds > 0 THEN p_interval_seconds END;
BEGIN
  INSERT INTO core.maintenance_schedule (name) VALUES ('default') ON CONFLICT DO NOTHING;
  SELECT * INTO r FROM core.maintenance_schedule WHERE name = 'default' FOR UPDATE;
  iv := coalesce(iv, r.interval_seconds);
  IF NOT p_force AND iv IS NOT NULL AND r.last_started_at IS NOT NULL
     AND r.last_started_at + make_interval(secs => iv) > now() THEN
    RETURN QUERY SELECT false, r.last_started_at, r.last_started_at + make_interval(secs => iv);
    RETURN;
  END IF;
  UPDATE core.maintenance_schedule
     SET last_started_at = now(), interval_seconds = iv
   WHERE name = 'default';
  RETURN QUERY SELECT true, now(), now() + make_interval(secs => iv);
END
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".maintenance_finish(p_started_at timestamptz) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
SET "{STATE_SETTING}" = 'on'
AS $$
  UPDATE core.maintenance_schedule SET last_finished_at = now()
   WHERE name = 'default' AND last_started_at = p_started_at
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".maintenance_status()
RETURNS TABLE (last_started_at timestamptz, last_finished_at timestamptz,
               interval_seconds double precision, next_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
SET "{STATE_SETTING}" = 'on'
AS $$
  SELECT m.last_started_at, m.last_finished_at, m.interval_seconds,
         m.last_started_at + make_interval(secs => m.interval_seconds)
    FROM core.maintenance_schedule m WHERE m.name = 'default'
$$;
"""
    )
    for fn in (
        "maintenance_begin(double precision, boolean)",
        "maintenance_finish(timestamptz)",
        "maintenance_status()",
    ):
        op.execute(f'REVOKE ALL ON FUNCTION "{S}".{fn} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".{fn} TO {rls.APP_ROLE}')

    # ------------------------------------------------------------ 공간별 마지막 결과
    op.create_table(
        "maintenance_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        # 작업 이름 → 처리한 행 수 (예: {"purge_unconfirmed_postings": 2})
        sa.Column("counts", JSONB(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("workspace_id", name="uq_maintenance_results_workspace"),
        sa.CheckConstraint("jsonb_typeof(counts) = 'object'", name="ck_maintenance_results_counts"),
        sa.CheckConstraint("errors >= 0", name="ck_maintenance_results_errors"),
        schema=S,
    )
    for stmt in rls.workspace_isolation_sql(S, "maintenance_results"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "maintenance_results", "SELECT, INSERT, UPDATE"))


def downgrade() -> None:
    op.drop_table("maintenance_results", schema=S)
    op.execute(f'DROP FUNCTION "{S}".maintenance_status()')
    op.execute(f'DROP FUNCTION "{S}".maintenance_finish(timestamptz)')
    op.execute(f'DROP FUNCTION "{S}".maintenance_begin(double precision, boolean)')
    op.drop_table("maintenance_schedule", schema=S)
    op.execute(f'DROP FUNCTION "{S}".next_job_due(text[])')
