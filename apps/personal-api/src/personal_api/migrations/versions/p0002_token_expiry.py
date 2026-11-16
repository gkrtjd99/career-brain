"""personal: API 토큰 만료일·마지막 사용 시각

Revision ID: p0002
Revises: p0001

- ``expires_at``: 선택. 지나면 ``lookup_api_token``이 찾지 않는다 (API·MCP 모두 거부).
- ``last_used_at``: 인증에 쓰인 마지막 시각. 요청마다 쓰지 않고 5분에 한 번만 갱신한다
  (``personal_api.identity.TOUCH_INTERVAL``). 조회 함수가 지금 값을 같이 돌려줘 갱신이 필요할 때만 쓴다.
- ``lookup_api_token``은 돌려주는 칸이 늘어 다시 만든다: (공간, actor, 토큰 ID, 마지막 사용 시각).
  토큰 ID는 "지금 쓰는 토큰은 폐기하지 않는다"를 API에서 지키는 데 쓴다. 원문·해시는 돌려주지 않는다.
- 이 표는 ``personal`` schema(개인 앱 소유)라 코어 migration(c00xx)이 아니라 여기서 바꾼다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "p0002"
down_revision = "p0001"
branch_labels = None
depends_on = None

S = "personal"


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("api_tokens", sa.Column("last_used_at", sa.DateTime(timezone=True)), schema=S)
    op.create_check_constraint(
        "ck_api_tokens_expires",
        "api_tokens",
        "expires_at is null or expires_at > created_at",
        schema=S,
    )
    op.execute(f'DROP FUNCTION "{S}".lookup_api_token(text)')
    op.execute(
        f"""
CREATE FUNCTION "{S}".lookup_api_token(p_token_hash text)
RETURNS TABLE (workspace_id uuid, actor_id uuid, token_id uuid, last_used_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT t.workspace_id, t.actor_id, t.id, t.last_used_at
  FROM personal.api_tokens t
  WHERE t.token_hash = p_token_hash
    AND t.revoked_at IS NULL
    AND (t.expires_at IS NULL OR t.expires_at > now())
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".lookup_api_token(text) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".lookup_api_token(text) TO {rls.APP_ROLE}')


def downgrade() -> None:
    op.execute(f'DROP FUNCTION "{S}".lookup_api_token(text)')
    op.execute(
        f"""
CREATE FUNCTION "{S}".lookup_api_token(p_token_hash text)
RETURNS TABLE (workspace_id uuid, actor_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT t.workspace_id, t.actor_id
  FROM personal.api_tokens t
  WHERE t.token_hash = p_token_hash AND t.revoked_at IS NULL
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".lookup_api_token(text) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".lookup_api_token(text) TO {rls.APP_ROLE}')
    op.drop_constraint("ck_api_tokens_expires", "api_tokens", schema=S)
    op.drop_column("api_tokens", "last_used_at", schema=S)
    op.drop_column("api_tokens", "expires_at", schema=S)
