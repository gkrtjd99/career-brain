"""personal: 이 컴퓨터에서 바로 열기 (bootstrap·토큰 붙여넣기 없이)

Revision ID: p0003
Revises: p0002

- ``local_login_owner()``: 이 설치에서 "내 공간"으로 열 공간과 그 owner. 앱 계정은 RLS 때문에 공간 목록을
  못 보므로 SECURITY DEFINER 함수 하나로만 고른다. 공간 ID·actor ID만 돌려준다 (토큰·해시는 아님).
- 고르는 순서: 아직 쓸 수 있는 토큰이 있는 공간 중 owner의 토큰, 데모(``demo``)가 아닌 것 먼저,
  마지막으로 쓴(없으면 만든) 시각이 늦은 것.
- 실제로 부르는 곳은 ``POST /api/local/session`` 하나다. 이 컴퓨터(loopback)에서 온, 같은 화면의 요청만
  받는다 (``personal_api.local_login``).
"""

from alembic import op
from career_core.db import rls

revision = "p0003"
down_revision = "p0002"
branch_labels = None
depends_on = None

S = "personal"


def upgrade() -> None:
    op.execute(
        f"""
CREATE FUNCTION "{S}".local_login_owner()
RETURNS TABLE (workspace_id uuid, actor_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT t.workspace_id, t.actor_id
  FROM personal.api_tokens t
  JOIN core.memberships m
    ON m.workspace_id = t.workspace_id AND m.actor_id = t.actor_id AND m.role = 'owner'
  WHERE t.revoked_at IS NULL
    AND (t.expires_at IS NULL OR t.expires_at > now())
  ORDER BY (t.label = 'demo'), coalesce(t.last_used_at, t.created_at) DESC
  LIMIT 1
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".local_login_owner() FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".local_login_owner() TO {rls.APP_ROLE}')


def downgrade() -> None:
    op.execute(f'DROP FUNCTION "{S}".local_login_owner()')
