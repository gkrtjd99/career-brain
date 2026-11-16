"""core: workspaces, actors, memberships + RLS

Revision ID: c0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0001"
down_revision = None
branch_labels = None
depends_on = None

S = "core"


def _ts() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.execute(rls.ensure_app_role_sql())
    op.execute(f'GRANT USAGE ON SCHEMA "{S}" TO {rls.APP_ROLE}')
    op.execute(rls.current_workspace_function_sql(S))
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".current_workspace_id() TO {rls.APP_ROLE}')

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        _ts(),
        schema=S,
    )
    op.create_table(
        "actors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        _ts(),
        schema=S,
    )
    op.create_table(
        "memberships",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.actors.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        _ts(),
        sa.CheckConstraint("role in ('owner', 'editor', 'viewer')", name="ck_memberships_role"),
        schema=S,
    )
    op.create_index("ix_memberships_actor_id", "memberships", ["actor_id"], schema=S)

    for stmt in rls.workspace_isolation_sql(S, "workspaces", column="id"):
        op.execute(stmt)
    for stmt in rls.workspace_isolation_sql(S, "memberships"):
        op.execute(stmt)

    # actors에는 workspace_id가 없다: 현재 공간의 membership이 있는 actor만 보인다.
    visible = f"EXISTS (SELECT 1 FROM {S}.memberships m WHERE m.actor_id = actors.id)"
    op.execute(f'ALTER TABLE "{S}".actors ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{S}".actors FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY actors_select ON "{S}".actors FOR SELECT USING ({visible})')
    op.execute(f'CREATE POLICY actors_insert ON "{S}".actors FOR INSERT WITH CHECK (true)')
    op.execute(f'CREATE POLICY actors_update ON "{S}".actors FOR UPDATE USING ({visible})')

    for table in ("workspaces", "actors", "memberships"):
        op.execute(rls.grant_app_sql(S, table))


def downgrade() -> None:
    op.drop_table("memberships", schema=S)
    op.drop_table("actors", schema=S)
    op.drop_table("workspaces", schema=S)
    op.execute(f'DROP FUNCTION "{S}".current_workspace_id()')
