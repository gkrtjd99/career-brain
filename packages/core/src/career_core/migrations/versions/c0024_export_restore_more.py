"""core: 웹 복원 작업 상태를 DB에 (여러 프로세스·재시작에 걸쳐)

Revision ID: c0024
Revises: c0021

- ``core.restore_jobs``: 웹 복원(``/core/maintenance/restore``) 작업 하나 = 올린 묶음 하나. 공간마다 하나
  (``UNIQUE(workspace_id)``, 새 업로드가 옛 작업을 바꾼다). 상태·단계·진행 수·확인 보고와 오류만 둔다.
  올린 파일은 설정한 공유 폴더(``CAREER_RESTORE_DIR``)의 ``career-restore-upload-<id>``에 있고, 이 표에는
  경로를 두지 않는다 (작업 ID에서 정해진다). age 신원은 저장하지 않는다.
- 실행 중(``verifying``·``applying``)인 작업은 돌리는 프로세스가 ``heartbeat_at``을 몇 초마다 고친다.
  그 시각이 오래된 실행 중 작업은 읽는 쪽이 ``failed``/``interrupted``로 닫는다 (재시작·죽은 프로세스).
- 일반 공간 표 (FORCE RLS, ``workspace_isolation``). 내보내기에서는 뺀다 (설치의 일시 상태:
  ``usecases/export.py`` ``EXCLUDED_TABLES``). 묶음 변환 단계는 ``no_data_change("c0024")``.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0024"
down_revision = "c0021"
branch_labels = None
depends_on = None

S = "core"
STATUSES = ("uploaded", "verifying", "verified", "applying", "done", "failed")


def upgrade() -> None:
    op.create_table(
        "restore_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text()),
        sa.Column("done", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("filename", sa.Text()),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("on_duplicate", sa.Text()),
        sa.Column("report", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("error_detail", sa.Text()),
        # 실행 중일 때 돌리는 프로세스 (표시·진단용 임의 ID)와 마지막 생존 신호
        sa.Column("runner", sa.Text()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", name="uq_restore_jobs_workspace"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_restore_jobs_status",
        ),
        sa.CheckConstraint(
            "on_duplicate IS NULL OR on_duplicate IN ('skip', 'fail')",
            name="ck_restore_jobs_on_duplicate",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_restore_jobs_sha256"),
        sa.CheckConstraint(
            "bytes >= 0 AND done >= 0 AND total >= 0 AND deletions >= 0",
            name="ck_restore_jobs_counts",
        ),
        sa.CheckConstraint(
            "report IS NULL OR jsonb_typeof(report) = 'object'", name="ck_restore_jobs_report"
        ),
        schema=S,
    )
    for stmt in rls.workspace_isolation_sql(S, "restore_jobs"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "restore_jobs", "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("restore_jobs", schema=S)
