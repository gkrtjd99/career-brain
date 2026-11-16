"""core: 스캔 PDF 비전 호출 전 비용 확인 (job_fetch_confirmations, needs_confirmation 상태)

Revision ID: c0025
Revises: c0021

- ``core.job_fetch_confirmations``: 비전 호출 예상 비용(쪽·이미지 수·토큰 추정·USD)이 상한을 넘은 가져오기
  실행의 확인 요청. 사람이 확인하면 다음 가져오기가 같은 파일·같은 쪽·그 금액 이하일 때 한 번만 쓴다.
  FORCE RLS(공간 문맥), 앱 계정은 SELECT·INSERT·UPDATE만 (지우기는 공고를 지울 때 CASCADE로만).
- ``core.job_fetch_runs.status``에 ``needs_confirmation``을 더한다.
- 공간을 넘는 조회가 없어 SECURITY DEFINER 함수는 없다. 표는 내보내기가 자동으로 따라간다.

나머지 c0025 작업(JWT introspection·캐시 시간, SSE 공간별 묶어 읽기, 토큰 이름·만료 연장, 선택형 문항,
모르는 봇 확인 화면)은 스키마를 바꾸지 않는다 (선택형 문항은 기존 ``fields``·``extracted`` JSON의 새 키).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0025"
down_revision = "c0021"
branch_labels = None
depends_on = None

S = "core"
STATUSES = ("pending", "confirmed", "declined", "used", "superseded")


def _fk(column: str, parent: str, ondelete: str = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.drop_constraint("ck_job_fetch_runs_status", "job_fetch_runs", schema=S)
    op.create_check_constraint(
        "ck_job_fetch_runs_status",
        "job_fetch_runs",
        "status in ('pending', 'done', 'listing', 'failed', 'needs_confirmation')",
        schema=S,
    )
    op.create_table(
        "job_fetch_confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("fetch_run_id", sa.Uuid(), nullable=False),
        sa.Column("used_run_id", sa.Uuid()),
        sa.Column("kind", sa.Text(), nullable=False, server_default="vision_cost"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("pages", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pages_total", sa.Integer(), nullable=False),
        sa.Column("images", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_usd", sa.Numeric(18, 10)),
        sa.Column("max_usd", sa.Numeric(18, 10)),
        sa.Column("threshold_usd", sa.Numeric(18, 10), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Uuid()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("kind in ('vision_cost')", name="ck_job_fetch_confirmations_kind"),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_job_fetch_confirmations_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(pages) = 'array' AND jsonb_array_length(pages) <= 64",
            name="ck_job_fetch_confirmations_pages",
        ),
        sa.CheckConstraint(
            "images >= 0 AND input_tokens >= 0 AND output_tokens >= 0 AND pages_total >= 0",
            name="ck_job_fetch_confirmations_counts",
        ),
        sa.CheckConstraint(
            "(status in ('pending', 'superseded')) = (decided_at IS NULL)",
            name="ck_job_fetch_confirmations_decided",
        ),
        _fk("posting_id", "job_postings"),
        _fk("fetch_run_id", "job_fetch_runs"),
        _fk("used_run_id", "job_fetch_runs", "SET NULL"),
        schema=S,
    )
    op.create_index(
        "ix_job_fetch_confirmations_posting",
        "job_fetch_confirmations",
        ["posting_id", "status"],
        schema=S,
    )
    # 한 실행에 확인 요청은 하나
    op.create_index(
        "uq_job_fetch_confirmations_run",
        "job_fetch_confirmations",
        ["fetch_run_id"],
        unique=True,
        schema=S,
    )
    for stmt in rls.workspace_isolation_sql(S, "job_fetch_confirmations"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "job_fetch_confirmations", "SELECT, INSERT, UPDATE"))


def downgrade() -> None:
    op.drop_table("job_fetch_confirmations", schema=S)
    # 확인 대기 실행은 직접 확인으로 돌린다. FORCE RLS는 소유자에게도 걸리므로 잠시 끈다 (c0021과 같은 방식)
    op.execute(f'ALTER TABLE "{S}"."job_fetch_runs" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        f"UPDATE \"{S}\".job_fetch_runs SET status = 'failed', error_code = 'needs_manual_check' "
        "WHERE status = 'needs_confirmation'"
    )
    op.execute(f'ALTER TABLE "{S}"."job_fetch_runs" FORCE ROW LEVEL SECURITY')
    op.drop_constraint("ck_job_fetch_runs_status", "job_fetch_runs", schema=S)
    op.create_check_constraint(
        "ck_job_fetch_runs_status",
        "job_fetch_runs",
        "status in ('pending', 'done', 'listing', 'failed')",
        schema=S,
    )
