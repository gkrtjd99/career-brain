"""core: search (pg_trgm indexes, pgvector embeddings)

Revision ID: c0005
Revises: c0004
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from career_core.db.types import Vector

revision = "c0005"
down_revision = "c0004"
branch_labels = None
depends_on = None

S = "core"
TARGET_KINDS = ("claim", "block", "answer", "job")


def upgrade() -> None:
    # 둘 다 PostgreSQL 13+에서 DB 소유자가 만들 수 있다 (pg_trgm은 trusted, pgvector는 superuser 필요할 수 있음).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 키워드 검색: 한국어 부분 문자열·오타에 강한 trigram 색인. 원본 테이블을 그대로 검색한다.
    for table, column in (
        ("claims", "statement"),
        ("document_blocks", "text"),
        ("interview_answers", "answer"),
    ):
        op.execute(
            f"CREATE INDEX ix_{table}_{column}_trgm ON {S}.{table} "
            f"USING gin ({column} gin_trgm_ops)"
        )

    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        # 모델·차원이 같을 때만 같은 공간이다. 검색은 한 공간 안에서만 한다.
        sa.Column("space", sa.Text(), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        # 임베딩한 본문의 sha256. 본문이 바뀌면 검색에서 자동으로 빠진다(다시 색인할 때까지).
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "target_kind", "target_id", "space"),
        sa.CheckConstraint(
            "target_kind in (" + ", ".join(f"'{k}'" for k in TARGET_KINDS) + ")",
            name="ck_embeddings_target_kind",
        ),
        sa.CheckConstraint("vector_dims(embedding) = dims", name="ck_embeddings_dims"),
        schema=S,
    )
    op.create_index("ix_embeddings_space", "embeddings", ["workspace_id", "space"], schema=S)
    for stmt in rls.workspace_isolation_sql(S, "embeddings"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "embeddings"))


def downgrade() -> None:
    op.drop_table("embeddings", schema=S)
    for table, column in (
        ("claims", "statement"),
        ("document_blocks", "text"),
        ("interview_answers", "answer"),
    ):
        op.execute(f"DROP INDEX IF EXISTS {S}.ix_{table}_{column}_trgm")
