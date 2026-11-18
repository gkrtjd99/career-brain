"""core: 브레인에 넣으면 알아서 정리 — 읽기가 끝난 내 경험 자료의 경험 후보 뽑기를 서버가 건다 (2026-11-18)

Revision ID: c0052
Revises: c0051

- ``core.workspace_settings.auto_extract``: 이 공간에서 읽기가 끝난 내 경험 자료를 바로 AI로 정리할지
  (기본 켜짐). 끄면 자료 화면에서 직접 "경험 뽑기"를 누른다.
- ``core.parse_runs.auto_extract``: 넣은 사람이 경험 뽑기 권한(``brain.extract`` = ``egress``)을 가졌는지.
  읽기를 끝낸 worker는 넣은 사람의 권한을 모르므로, 넣을 때 정해 둔다. 옛 행은 ``false``(자동으로 뽑지 않음).
"""

import sqlalchemy as sa
from alembic import op

revision = "c0052"
down_revision = "c0051"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("auto_extract", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=S,
    )
    op.add_column(
        "parse_runs",
        sa.Column("auto_extract", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("parse_runs", "auto_extract", schema=S)
    op.drop_column("workspace_settings", "auto_extract", schema=S)
