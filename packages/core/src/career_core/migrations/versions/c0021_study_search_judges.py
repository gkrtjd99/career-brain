"""core: 공간 공용 설정(표시 시간대·기술명 별칭) — study_settings를 넓힌다

Revision ID: c0021
Revises: c0020

- ``core.workspace_settings`` (공간당 1행): 표시 시간대(``timezone``, NULL이면 기본 Asia/Seoul)와
  사용자 기술명 별칭(``tech_aliases``, ``[{term, aliases}]``, 기본 표 위에 합쳐진다).
- c0013 ``study_settings.timezone``을 옮기고 ``study_settings``를 지운다. 학습 API
  (``/core/study/settings``)는 같은 모양으로 이 표를 읽고 쓴다.
- 옮기는 동안만 두 표를 NO FORCE로 둔다: 소유자가 superuser가 아니어도(FORCE RLS가 소유자에게도
  걸린다) 공간 문맥 없이 모든 공간의 행을 옮길 수 있게. 끝나면 새 표는 FORCE로 둔다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0021"
down_revision = "c0020"
branch_labels = None
depends_on = None

S = "core"


def _workspace_fk() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _times() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "workspace_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("timezone", sa.Text(), nullable=True),
        sa.Column("tech_aliases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_settings_workspace"),
        sa.CheckConstraint(
            "timezone IS NULL OR length(timezone) between 1 and 64",
            name="ck_workspace_settings_timezone",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tech_aliases) = 'array' AND jsonb_array_length(tech_aliases) <= 200",
            name="ck_workspace_settings_tech_aliases",
        ),
        schema=S,
    )
    # 옮기기: 두 표 모두 FORCE 없이 (소유자는 정책을 받지 않는다)
    op.execute(f'ALTER TABLE "{S}"."study_settings" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
INSERT INTO "{S}".workspace_settings
    (id, workspace_id, timezone, tech_aliases, updated_by, created_at, updated_at)
SELECT id, workspace_id, timezone, '[]'::jsonb, updated_by, created_at, updated_at
FROM "{S}".study_settings
"""
    )
    for stmt in rls.workspace_isolation_sql(S, "workspace_settings"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "workspace_settings", "SELECT, INSERT, UPDATE"))
    op.drop_table("study_settings", schema=S)


def downgrade() -> None:
    op.create_table(
        "study_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", name="uq_study_settings_workspace"),
        sa.CheckConstraint("length(timezone) between 1 and 64", name="ck_study_settings_timezone"),
        schema=S,
    )
    op.execute(f'ALTER TABLE "{S}"."workspace_settings" NO FORCE ROW LEVEL SECURITY')
    # 시간대를 저장한 공간만 되돌린다 (별칭은 c0021 이전에 둘 곳이 없다)
    op.execute(
        f"""
INSERT INTO "{S}".study_settings (id, workspace_id, timezone, updated_by, created_at, updated_at)
SELECT id, workspace_id, timezone, updated_by, created_at, updated_at
FROM "{S}".workspace_settings WHERE timezone IS NOT NULL
"""
    )
    for stmt in rls.workspace_isolation_sql(S, "study_settings"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "study_settings", "SELECT, INSERT, UPDATE"))
    op.drop_table("workspace_settings", schema=S)
