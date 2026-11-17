"""core: 공통 답변 — 지원을 넘어 다시 쓰는 내 글 (plan §19.4 ``answer_assets``, §20 "나 › 내 글")

Revision ID: c0042
Revises: c0041

- ``core.answer_assets``: 1분 자기소개·지원 동기 틀·성격 장단점·갈등 경험·실패 경험·입사 후 포부·기타.
  제목(1–200자)·본문(20,000자 이하, 사용자가 쓴 글)·어디서 시작했나(``source_submission_id`` 제출본 +
  ``source_document_id`` 그 안의 문서, 제출본을 지우지 않으므로 SET NULL만 둔다).
- ``core.answer_asset_claims``: 근거로 이은 확인한 사실(주장). ``UNIQUE (answer_asset_id, claim_id)``. 주장이
  확인된 상태일 때만 잇는 것은 usecase가 본다 (문서 근거 ``document_claims``와 같은 규칙).
- ``core.answer_asset_uses``: 지원서에 넣은 기록(지원·문서·만든 revision). 지원·문서가 지워지면 함께 지운다.
- 세 표 모두 FORCE RLS, 앱 role ``SELECT, INSERT, UPDATE, DELETE``. 다른 공간 행은 복합 FK가 막는다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0042"
down_revision = "c0041"
branch_labels = None
depends_on = None

S = "core"
KINDS = ("intro", "motivation", "personality", "conflict", "failure", "aspiration", "other")


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _fk(table: str, name: str, column: str, parent: str, action: str) -> str:
    return (
        f'ALTER TABLE "{S}".{table} ADD CONSTRAINT {name} '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}".{parent} (workspace_id, id) '
        f"ON DELETE {action}"
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    op.create_table(
        "answer_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_submission_id", sa.Uuid(), nullable=True),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_answer_assets_kind"),
        sa.CheckConstraint("length(title) between 1 and 200", name="ck_answer_assets_title"),
        sa.CheckConstraint("length(body) <= 20000", name="ck_answer_assets_body"),
        schema=S,
    )
    op.execute(
        _fk(
            "answer_assets",
            "fk_answer_assets_submission",
            "source_submission_id",
            "application_submissions",
            "SET NULL (source_submission_id)",
        )
    )
    op.create_table(
        "answer_asset_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("answer_asset_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("answer_asset_id", "claim_id", name="uq_answer_asset_claims"),
        schema=S,
    )
    op.execute(
        _fk(
            "answer_asset_claims",
            "fk_answer_asset_claims_asset",
            "answer_asset_id",
            "answer_assets",
            "CASCADE",
        )
    )
    op.execute(
        _fk("answer_asset_claims", "fk_answer_asset_claims_claim", "claim_id", "claims", "CASCADE")
    )
    op.create_index(
        "ix_answer_asset_claims_claim",
        "answer_asset_claims",
        ["workspace_id", "claim_id"],
        schema=S,
    )
    op.create_table(
        "answer_asset_uses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("answer_asset_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("workspace_id", "id"),
        schema=S,
    )
    uses = (
        ("fk_answer_asset_uses_asset", "answer_asset_id", "answer_assets", "CASCADE"),
        ("fk_answer_asset_uses_application", "application_id", "applications", "CASCADE"),
        ("fk_answer_asset_uses_document", "document_id", "application_documents", "CASCADE"),
        (
            "fk_answer_asset_uses_revision",
            "revision_id",
            "document_revisions",
            "SET NULL (revision_id)",
        ),
    )
    for name, column, parent, action in uses:
        op.execute(_fk("answer_asset_uses", name, column, parent, action))
    op.create_index(
        "ix_answer_asset_uses_asset",
        "answer_asset_uses",
        ["workspace_id", "answer_asset_id"],
        schema=S,
    )
    for table in ("answer_assets", "answer_asset_claims", "answer_asset_uses"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("answer_asset_uses", schema=S)
    op.drop_table("answer_asset_claims", schema=S)
    op.drop_table("answer_assets", schema=S)
