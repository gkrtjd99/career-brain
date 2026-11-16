"""core: model_calls (모델 호출 장부)

Revision ID: c0003
Revises: c0002
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0003"
down_revision = "c0002"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("pricing_provider", sa.Text(), nullable=True),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("served_model", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False),
        sa.Column("billing", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(18, 10), nullable=True),
        sa.Column("cost_basis", sa.Text(), nullable=False),
        sa.Column("price_refs", ARRAY(sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status in ('ok', 'error', 'denied')", name="ck_model_calls_status"),
        sa.CheckConstraint(
            "billing in ('metered', 'subscription', 'free')", name="ck_model_calls_billing"
        ),
        sa.CheckConstraint(
            "cost_basis in ('provider_reported', 'metered', 'estimated', "
            "'list_price_equivalent', 'unknown', 'none')",
            name="ck_model_calls_cost_basis",
        ),
        sa.CheckConstraint("cost_usd is null or cost_usd >= 0", name="ck_model_calls_cost"),
        schema=S,
    )
    op.create_index(
        "ix_model_calls_ws_created", "model_calls", ["workspace_id", "created_at"], schema=S
    )
    for stmt in rls.workspace_isolation_sql(S, "model_calls"):
        op.execute(stmt)
    # 장부는 추가만 한다. 앱 role에 UPDATE/DELETE를 주지 않는다.
    op.execute(rls.grant_app_sql(S, "model_calls", "SELECT, INSERT"))


def downgrade() -> None:
    op.drop_table("model_calls", schema=S)
