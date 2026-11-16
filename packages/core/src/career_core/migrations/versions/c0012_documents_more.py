"""core: 공용 문서(지원에 묶이지 않은 문서)와 복사 출처, 수정안 멱등 키, document_settings, application_stages

Revision ID: c0012
Revises: c0011
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0012"
down_revision = "c0011"
branch_labels = None
depends_on = None

S = "core"

STAGE_KINDS = ("document", "test", "assignment", "interview", "final", "other")
STAGE_OUTCOMES = ("pending", "passed", "failed", "withdrawn", "cancelled")

# 복사 출처 FK 두 개는 application_documents → (자기 자신, document_revisions → application_documents)로
# 표 순환을 만든다. c0011처럼 DEFERRABLE INITIALLY IMMEDIATE로 두어 복원 때만 트랜잭션 끝에서 검사한다.
_DEFERRABLE = (
    ("application_documents", "fk_application_documents_source_document"),
    ("application_documents", "fk_application_documents_source_revision"),
)


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _fk(column: str, parent: str, ondelete: str | None = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def _in(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} in ({listed})", name=name)


def upgrade() -> None:
    # --- 공용 문서: application_id가 NULL이면 공간 공용 문서(포트폴리오·기본 이력서).
    # revision·근거 연결·수정안 테이블은 그대로 같이 쓴다 (같은 base_revision/409·근거 규칙).
    op.alter_column("application_documents", "application_id", nullable=True, schema=S)
    op.add_column("application_documents", sa.Column("source_document_id", sa.Uuid()), schema=S)
    op.add_column("application_documents", sa.Column("source_revision_id", sa.Uuid()), schema=S)
    op.create_check_constraint(
        "ck_application_documents_shared_kind",
        "application_documents",
        "kind <> 'question' or application_id is not null",
        schema=S,
    )
    op.create_check_constraint(
        "ck_application_documents_source",
        "application_documents",
        "(source_document_id is null) = (source_revision_id is null)",
        schema=S,
    )
    # 복사 출처: 원본 문서와 그때의 revision. 원본·revision은 지우지 않는다(NO ACTION).
    op.create_foreign_key(
        "fk_application_documents_source_document",
        "application_documents",
        "application_documents",
        ["workspace_id", "source_document_id"],
        ["workspace_id", "id"],
        source_schema=S,
        referent_schema=S,
    )
    op.create_foreign_key(
        "fk_application_documents_source_revision",
        "application_documents",
        "document_revisions",
        ["workspace_id", "source_revision_id"],
        ["workspace_id", "id"],
        source_schema=S,
        referent_schema=S,
    )
    for table, name in _DEFERRABLE:
        op.execute(
            f'ALTER TABLE "{S}"."{table}" ALTER CONSTRAINT {name} DEFERRABLE INITIALLY IMMEDIATE'
        )
    op.create_index(
        "ix_application_documents_shared",
        "application_documents",
        ["workspace_id"],
        schema=S,
        postgresql_where=sa.text("application_id is null"),
    )

    # --- 수정안 멱등 키: 같은 키·같은 문단이면 다시 만들지 않는다 (실행 재개·요청 재시도).
    op.add_column("document_suggestions", sa.Column("idempotency_key", sa.Text()), schema=S)
    op.create_unique_constraint(
        "uq_document_suggestions_idempotency",
        "document_suggestions",
        ["document_id", "idempotency_key", "block_id"],
        schema=S,
    )

    # --- 공간의 문서 설정 1행: 상투어 목록, 본인 연락처(민감 정보 재검사에서 뺄 값).
    op.create_table(
        "document_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        # NULL이면 기본 목록(domain.documents.DEFAULT_CLICHES). 값이 있으면 그 목록이 기본을 대신한다.
        sa.Column("cliches", JSONB()),
        # {"emails": [...], "phones": [...]} — 제출 전 검사에서 본인 연락처는 경고하지 않는다
        sa.Column("own_contacts", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id"),
        sa.CheckConstraint(
            "cliches is null or jsonb_typeof(cliches) = 'array'",
            name="ck_document_settings_cliches",
        ),
        schema=S,
    )

    # --- 전형 단계: 제출본에 묶인 서류·시험·면접 일정과 결과. 결과만 기록하고 원인을 단정하지 않는다.
    op.create_table(
        "application_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("kind", STAGE_KINDS, "ck_application_stages_kind"),
        _in("outcome", STAGE_OUTCOMES, "ck_application_stages_outcome"),
        sa.CheckConstraint(
            "(outcome = 'pending') = (decided_at is null)", name="ck_application_stages_decided"
        ),
        sa.CheckConstraint("position >= 0", name="ck_application_stages_position"),
        _fk("application_id", "applications"),
        _fk("submission_id", "application_submissions", None),
        schema=S,
    )
    op.create_index(
        "ix_application_stages_application", "application_stages", ["application_id"], schema=S
    )

    for table in ("document_settings", "application_stages"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "document_settings", "SELECT, INSERT, UPDATE"))
    op.execute(rls.grant_app_sql(S, "application_stages"))


def downgrade() -> None:
    op.drop_table("application_stages", schema=S)
    op.drop_table("document_settings", schema=S)
    op.drop_constraint(
        "uq_document_suggestions_idempotency", "document_suggestions", schema=S, type_="unique"
    )
    op.drop_column("document_suggestions", "idempotency_key", schema=S)
    op.drop_index("ix_application_documents_shared", "application_documents", schema=S)
    for _, name in _DEFERRABLE:
        op.drop_constraint(name, "application_documents", schema=S, type_="foreignkey")
    op.drop_constraint("ck_application_documents_source", "application_documents", schema=S)
    op.drop_constraint("ck_application_documents_shared_kind", "application_documents", schema=S)
    op.drop_column("application_documents", "source_revision_id", schema=S)
    op.drop_column("application_documents", "source_document_id", schema=S)
    # 공용 문서가 남아 있으면 NOT NULL로 되돌릴 수 없다 (지우지 않고 실패한다).
    op.alter_column("application_documents", "application_id", nullable=False, schema=S)
