"""core: 작업 큐 깨우기(NOTIFY trigger), 유지보수용 공간 목록 함수

Revision ID: c0015
Revises: c0011

- ``core.jobs``에 대기(queued) 행이 생기거나 다시 대기로 돌아오면 ``career_jobs`` 채널로
  NOTIFY한다. 알림은 commit 때 전달되고 rollback이면 사라진다. 내용은 작업 종류와
  "몇 초 뒤 집을 수 있는지"뿐이다 (LISTEN은 권한 검사가 없으므로 공간·대상 ID를 넣지 않는다).
  알림은 깨우기 힌트일 뿐이고, 정확성은 worker의 긴 간격 확인(fallback poll)이 지킨다.
- ``core.maintenance_workspace_ids()``: 주기 유지보수(초안 정리 등)가 모든 공간을 돌 수 있게
  공간 ID만 돌려준다. ``workspaces``는 FORCE RLS라 소유자도 정책을 받으므로, 이 함수 안에서만
  켜지는 설정(``career.maintenance_scan``)을 보는 소유자 전용 SELECT 정책을 하나 더 둔다.
"""

from alembic import op
from career_core.db import rls

revision = "c0015"
down_revision = "c0011"
branch_labels = None
depends_on = None

S = "core"
CHANNEL = "career_jobs"  # career_core.jobs.wake.JOB_CHANNEL
SCAN_SETTING = "career.maintenance_scan"


def upgrade() -> None:
    op.execute(
        f"""
CREATE FUNCTION "{S}".notify_job_ready() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  PERFORM pg_notify(
    '{CHANNEL}',
    NEW.kind || ' ' ||
      round(greatest(0, extract(epoch FROM NEW.available_at - now()))::numeric, 3)::text);
  RETURN NULL;
END
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".notify_job_ready() FROM PUBLIC')
    # INSERT, 그리고 status·available_at을 바꾼 UPDATE만 (heartbeat의 locked_until 연장은 제외)
    op.execute(
        f"""
CREATE TRIGGER jobs_notify_ready
AFTER INSERT OR UPDATE OF status, available_at ON "{S}".jobs
FOR EACH ROW WHEN (NEW.status = 'queued')
EXECUTE FUNCTION "{S}".notify_job_ready();
"""
    )

    op.execute(
        f"""
CREATE POLICY maintenance_scan ON "{S}".workspaces FOR SELECT TO CURRENT_USER
USING (current_setting('{SCAN_SETTING}', true) = 'on');
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".maintenance_workspace_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
SET "{SCAN_SETTING}" = 'on'
AS $$ SELECT w.id FROM core.workspaces w ORDER BY w.id $$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".maintenance_workspace_ids() FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".maintenance_workspace_ids() TO {rls.APP_ROLE}')


def downgrade() -> None:
    op.execute(f'DROP FUNCTION "{S}".maintenance_workspace_ids()')
    op.execute(f'DROP POLICY maintenance_scan ON "{S}".workspaces')
    op.execute(f'DROP TRIGGER jobs_notify_ready ON "{S}".jobs')
    op.execute(f'DROP FUNCTION "{S}".notify_job_ready()')
