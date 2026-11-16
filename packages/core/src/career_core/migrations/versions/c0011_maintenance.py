"""core: export_runs (내보내기·복원 기록), 순환 FK 2개를 DEFERRABLE로

Revision ID: c0011
Revises: c0009
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0011"
down_revision = "c0009"
branch_labels = None
depends_on = None

S = "core"

_DEFERRABLE = (
    ("job_postings", "fk_job_postings_confirmed_version"),
    ("document_revisions", "fk_document_revisions_suggestion"),
)


def upgrade() -> None:
    # 내보내기·복원 1회 = 1행. 마지막 백업 시각을 보여 주는 용도. 묶음 파일 자체는 두지 않는다.
    op.create_table(
        "export_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("archive_sha256", sa.Text(), nullable=False),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=False),
        sa.Column("rows_total", sa.BigInteger(), nullable=False),
        sa.Column("blobs_total", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("kind in ('export', 'restore')", name="ck_export_runs_kind"),
        sa.CheckConstraint("via in ('cli', 'http')", name="ck_export_runs_via"),
        sa.CheckConstraint("archive_sha256 ~ '^[0-9a-f]{64}$'", name="ck_export_runs_sha256"),
        schema=S,
    )
    op.create_index(
        "ix_export_runs_workspace_created", "export_runs", ["workspace_id", "created_at"], schema=S
    )
    for stmt in rls.workspace_isolation_sql(S, "export_runs"):
        op.execute(stmt)
    # 기록은 고치지 않는다.
    op.execute(rls.grant_app_sql(S, "export_runs", "SELECT, INSERT"))

    # 순환 FK를 하나씩 미룰 수 있게 한다 (평소엔 지금처럼 즉시 검사).
    # 복원은 ID를 그대로 넣으므로 공고 ↔ 확정 버전, revision ↔ 수정안 순환을 트랜잭션 끝에서 검사한다.
    # 추가만 되는 표(revision)를 나중에 UPDATE하지 않고, CHECK(applied ⇔ 칸 있음)도 그대로 지킨다.
    for table, name in _DEFERRABLE:
        op.execute(
            f'ALTER TABLE "{S}"."{table}" ALTER CONSTRAINT {name} DEFERRABLE INITIALLY IMMEDIATE'
        )


def downgrade() -> None:
    for table, name in _DEFERRABLE:
        op.execute(f'ALTER TABLE "{S}"."{table}" ALTER CONSTRAINT {name} NOT DEFERRABLE')
    op.drop_table("export_runs", schema=S)
