"""core: 웹 복원을 worker 작업 큐로, 합치기의 비슷한 행 선택

Revision ID: c0027
Revises: c0025

- 웹 복원(``/core/maintenance/restore``)의 확인·합치기는 API 프로세스가 아니라 worker가 돈다 (작업 큐
  종류 ``restore``, 대상 = ``restore_jobs.id``). ``core.jobs.kind``에는 CHECK가 없어 큐 쪽 변경은 없다.
  worker는 그 작업을 시작한 사람으로 합친다: ``started_by``(시작할 때마다 고침, 올린 사람은 ``actor_id``
  그대로). 실행 때 그 사람의 지금 역할을 다시 확인한다.
- ``near_choices``: 합치기 때 고른 비슷한 행 쌍 (``[{table, incoming_id, existing_id}]``, 들어오는 행을
  건너뛰고 기존 행으로 잇는 쌍만). 확인(dry-run) 보고의 ``near_duplicates``에서 고른다. 없으면 기본(둘 다 둠).
- ``restore_jobs``는 내보내지 않는 표라 묶음 변환 단계는 ``no_data_change("c0027")``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0027"
down_revision = "c0025"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.add_column("restore_jobs", sa.Column("started_by", sa.Uuid()), schema=S)
    op.add_column("restore_jobs", sa.Column("near_choices", JSONB()), schema=S)
    op.create_check_constraint(
        "ck_restore_jobs_near_choices",
        "restore_jobs",
        "near_choices IS NULL OR (jsonb_typeof(near_choices) = 'array' "
        "AND jsonb_array_length(near_choices) <= 1000)",
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint("ck_restore_jobs_near_choices", "restore_jobs", schema=S)
    op.drop_column("restore_jobs", "near_choices", schema=S)
    op.drop_column("restore_jobs", "started_by", schema=S)
