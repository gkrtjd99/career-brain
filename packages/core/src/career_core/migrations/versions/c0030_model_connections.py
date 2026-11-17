"""core: 화면에서 만든 모델 연결·용도별 라우트 덮기·월 예산 덮기 (O22, plan §21)

Revision ID: c0030
Revises: c0029

- ``core.model_connections``: 공간마다 화면에서 만든 연결 (이름은 공간 안에서 유일). preset·주소·과금·
  환산 기준·토큰 파라미터·보낼 수 있는 자료(``data_classes``, ``restricted`` 금지)·키 끝 4자리·마지막 시험.
  키 자체는 이 표에 두지 않는다.
- ``core.model_connection_secrets``: 연결 하나의 키 (Fernet 암호문, 연결과 1:1, 지우면 같이 지워진다).
  내보내기 묶음에서 뺀다 (export.EXCLUDED_TABLES). 복원한 연결은 끝 4자리만 있고 암호문이 없으므로
  "다시 넣어야 함"(``needs_reentry``)으로 보인다.
- ``core.model_route_overrides``: 용도(purpose)마다 ``endpoint``/``model`` 덮기. 파일 엔드포인트도 가리킬 수
  있어 FK는 두지 않는다 (가리키는 연결이 없어지면 설정을 만들 때 건너뛴다).
- ``core.model_settings`` (공간당 1행): 월 예산 덮기(``monthly_usd``, NULL = 파일 값)와 ``version``.
  화면에서 무엇이든 바꾸면 ``version``을 올린다. worker가 몇 초마다 보고 바뀌었으면 설정을 다시 만든다.
- 모두 FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0030"
down_revision = "c0029"
branch_labels = None
depends_on = None

S = "core"
_NAME = "^[a-z][a-z0-9_-]{0,39}$"
_PRESETS = (
    "anthropic",
    "openai",
    "xai",
    "gemini",
    "cliproxyapi",
    "openrouter",
    "ollama",
    "openai-compatible",
    "local",
)


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
    presets = ", ".join(f"'{p}'" for p in _PRESETS)
    op.create_table(
        "model_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("preset", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("billing", sa.Text(), nullable=False),
        sa.Column("price_as", sa.Text(), nullable=True),
        sa.Column("token_param", sa.Text(), nullable=False),
        sa.Column(
            "data_classes",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("api_key_last4", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_model_connections_name"),
        sa.CheckConstraint(f"name ~ '{_NAME}'", name="ck_model_connections_name"),
        sa.CheckConstraint(f"preset IN ({presets})", name="ck_model_connections_preset"),
        sa.CheckConstraint(
            "billing IN ('metered', 'subscription', 'free')", name="ck_model_connections_billing"
        ),
        sa.CheckConstraint(
            "token_param IN ('max_tokens', 'max_completion_tokens')",
            name="ck_model_connections_token_param",
        ),
        sa.CheckConstraint(
            "data_classes <@ ARRAY['public', 'private']::text[]",
            name="ck_model_connections_data_classes",
        ),
        sa.CheckConstraint(
            "base_url IS NULL OR (length(base_url) <= 500 AND base_url ~ '^https?://')",
            name="ck_model_connections_base_url",
        ),
        sa.CheckConstraint(
            "api_key_last4 IS NULL OR length(api_key_last4) <= 4",
            name="ck_model_connections_last4",
        ),
        schema=S,
    )
    op.create_table(
        "model_connection_secrets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("connection_id", name="uq_model_connection_secrets_connection"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [f"{S}.model_connections.workspace_id", f"{S}.model_connections.id"],
            ondelete="CASCADE",
            name="fk_model_connection_secrets_connection",
        ),
        sa.CheckConstraint("length(ciphertext) <= 4096", name="ck_model_connection_secrets_size"),
        schema=S,
    )
    op.create_table(
        "model_route_overrides",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "purpose", name="uq_model_route_overrides_purpose"),
        sa.CheckConstraint(
            "purpose ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_model_route_overrides_purpose"
        ),
        sa.CheckConstraint(
            "length(endpoint) between 1 and 40 AND position('/' in endpoint) = 0",
            name="ck_model_route_overrides_endpoint",
        ),
        sa.CheckConstraint(
            "length(model) between 1 and 200", name="ck_model_route_overrides_model"
        ),
        schema=S,
    )
    op.create_table(
        "model_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("monthly_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", name="uq_model_settings_workspace"),
        sa.CheckConstraint(
            "monthly_usd IS NULL OR (monthly_usd >= 0 AND monthly_usd <= 1000000)",
            name="ck_model_settings_monthly_usd",
        ),
        sa.CheckConstraint("version >= 0", name="ck_model_settings_version"),
        schema=S,
    )
    for table in (
        "model_connections",
        "model_connection_secrets",
        "model_route_overrides",
        "model_settings",
    ):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("model_settings", schema=S)
    op.drop_table("model_route_overrides", schema=S)
    op.drop_table("model_connection_secrets", schema=S)
    op.drop_table("model_connections", schema=S)
