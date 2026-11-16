"""core: sources, source_versions, parse_runs, document_blocks, jobs

Revision ID: c0002
Revises: c0001
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0002"
down_revision = "c0001"
branch_labels = None
depends_on = None

S = "core"


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _scoped_fk(column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("data_class", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(
            "data_class in ('public', 'private', 'restricted')", name="ck_sources_data_class"
        ),
        schema=S,
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        # 같은 공간에서 같은 바이트는 한 번만 저장·처리한다. 공간이 다르면 별개다.
        sa.UniqueConstraint("workspace_id", "sha256"),
        _scoped_fk("source_id", "sources"),
        schema=S,
    )
    op.create_table(
        "parse_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("pages_total", sa.Integer()),
        sa.Column("provider", sa.Text()),
        sa.Column("provider_job_id", sa.Text()),
        sa.Column("requested_pages", ARRAY(sa.Integer())),
        sa.Column("missing_pages", ARRAY(sa.Integer()), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.Text()),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "source_version_id", "pipeline_version"),
        sa.CheckConstraint(
            "status in ('pending', 'waiting_provider', 'complete', 'partial', 'failed')",
            name="ck_parse_runs_status",
        ),
        _scoped_fk("source_version_id", "source_versions"),
        schema=S,
    )
    op.create_table(
        "document_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("parse_run_id", sa.Uuid(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        _scoped_fk("parse_run_id", "parse_runs"),
        schema=S,
    )
    op.create_index(
        "ix_document_blocks_run", "document_blocks", ["parse_run_id", "ordinal"], schema=S
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        _ts(),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed')", name="ck_jobs_status"
        ),
        schema=S,
    )
    op.create_index(
        "uq_jobs_active_target",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("status in ('queued', 'running')"),
    )
    op.create_index("ix_jobs_ready", "jobs", ["status", "available_at"], schema=S)

    for table in ("sources", "source_versions", "parse_runs", "document_blocks"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table))
    # worker가 공간 문맥 없이 다음 작업을 집는 claim_job(소유자 권한) 때문에 FORCE는 켜지 않는다.
    for stmt in rls.workspace_isolation_sql(S, "jobs", force=False):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "jobs", "SELECT, INSERT, UPDATE"))

    op.execute(
        f"""
CREATE FUNCTION "{S}".claim_job(p_kinds text[], p_lease_seconds integer)
RETURNS TABLE (job_id uuid, workspace_id uuid, kind text, target_id uuid, attempts integer)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  UPDATE core.jobs j
     SET status = 'running',
         attempts = j.attempts + 1,
         locked_until = now() + make_interval(secs => p_lease_seconds)
   WHERE j.id = (
     SELECT c.id FROM core.jobs c
      WHERE c.kind = ANY (p_kinds)
        AND c.available_at <= now()
        AND (c.status = 'queued' OR (c.status = 'running' AND c.locked_until < now()))
      ORDER BY c.available_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1)
  RETURNING j.id, j.workspace_id, j.kind, j.target_id, j.attempts
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".claim_job(text[], integer) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".claim_job(text[], integer) TO {rls.APP_ROLE}')


def downgrade() -> None:
    op.execute(f'DROP FUNCTION "{S}".claim_job(text[], integer)')
    for table in ("jobs", "document_blocks", "parse_runs", "source_versions", "sources"):
        op.drop_table(table, schema=S)
