"""personal: workspace-scoped API tokens

Revision ID: p0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "p0001"
down_revision = None
branch_labels = None
depends_on = None

S = "personal"


def upgrade() -> None:
    op.execute(f'GRANT USAGE ON SCHEMA "{S}" TO {rls.APP_ROLE}')
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        # 토큰은 실제 membership이 있는 (공간, actor)에만 연결된다. membership 삭제 시 함께 삭제.
        sa.ForeignKeyConstraint(
            ["workspace_id", "actor_id"],
            ["core.memberships.workspace_id", "core.memberships.actor_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    # 조회 함수(소유자 권한)만 공간 문맥 없이 hash로 찾을 수 있도록 FORCE는 켜지 않는다.
    for stmt in rls.workspace_isolation_sql(S, "api_tokens", force=False):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "api_tokens", "SELECT, INSERT, UPDATE"))
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


def downgrade() -> None:
    op.execute(f'DROP FUNCTION "{S}".lookup_api_token(text)')
    op.drop_table("api_tokens", schema=S)
