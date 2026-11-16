"""core: job_postings, job_posting_versions, job_fetch_runs

Revision ID: c0006
Revises: c0005
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0006"
down_revision = "c0005"
branch_labels = None
depends_on = None

S = "core"


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _fk(column: str, parent: str, ondelete: str = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        # 사용자가 확인한 값과 각 값의 근거. 확인 전에는 비어 있다.
        sa.Column("fields", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confirmed_version_id", sa.Uuid()),
        # 확인한 뒤 다시 가져오기에서 바뀐 항목. 사용자가 다시 확인하면 비운다.
        sa.Column(
            "pending_changes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("saved_by", sa.Uuid()),
        sa.Column("saved_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "canonical_url"),
        sa.CheckConstraint(
            "status in ('draft', 'saved', 'archived')", name="ck_job_postings_status"
        ),
        sa.CheckConstraint(
            "status <> 'saved' or (saved_by is not null and saved_at is not null)",
            name="ck_job_postings_saved",
        ),
        schema=S,
    )
    op.create_table(
        "job_posting_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("method", sa.Text(), nullable=False),  # http | browser | manual_text
        sa.Column("adapter", sa.Text(), nullable=False),  # json_ld | ninehire | text
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),  # 정리한 본문 (스냅샷)
        sa.Column("extracted", JSONB(), nullable=False),  # {항목: {value, evidence}}
        sa.Column("missing", JSONB(), nullable=False),  # 확인이 필요한 빈 항목
        sa.Column("changed_fields", JSONB(), nullable=False),  # 직전 버전과 달라진 항목
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("screenshot_key", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("posting_id", "version_no"),
        sa.UniqueConstraint("posting_id", "content_hash"),
        sa.CheckConstraint(
            "method in ('http', 'browser', 'manual_text')", name="ck_job_posting_versions_method"
        ),
        _fk("posting_id", "job_postings"),
        schema=S,
    )
    op.create_foreign_key(
        "fk_job_postings_confirmed_version",
        "job_postings",
        "job_posting_versions",
        ["workspace_id", "confirmed_version_id"],
        ["workspace_id", "id"],
        source_schema=S,
        referent_schema=S,
        ondelete="RESTRICT",
    )
    op.create_table(
        "job_fetch_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        sa.Column("version_id", sa.Uuid()),
        sa.Column("unchanged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candidates", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(
            "status in ('pending', 'done', 'listing', 'failed')", name="ck_job_fetch_runs_status"
        ),
        _fk("posting_id", "job_postings"),
        _fk("version_id", "job_posting_versions", "SET NULL"),
        schema=S,
    )
    op.create_index("ix_job_fetch_runs_posting", "job_fetch_runs", ["posting_id"], schema=S)
    for table in ("job_postings", "job_posting_versions", "job_fetch_runs"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "job_postings"))
    op.execute(rls.grant_app_sql(S, "job_fetch_runs"))
    # 스냅샷은 고치지 않는다. 공고 삭제(미확정 정리) 때만 함께 지워진다.
    op.execute(rls.grant_app_sql(S, "job_posting_versions", "SELECT, INSERT"))


def downgrade() -> None:
    op.drop_constraint("fk_job_postings_confirmed_version", "job_postings", schema=S)
    for table in ("job_fetch_runs", "job_posting_versions", "job_postings"):
        op.drop_table(table, schema=S)
