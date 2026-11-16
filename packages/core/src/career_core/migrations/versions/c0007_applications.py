"""core: applications, application_documents, document_revisions, document_claims,
document_suggestions, application_submissions

Revision ID: c0007
Revises: c0006
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0007"
down_revision = "c0006"
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


def _fk(column: str, parent: str, ondelete: str | None = "CASCADE") -> sa.ForeignKeyConstraint:
    """ondelete=None은 NO ACTION: 참조된 행(제출본·근거 주장)이 있으면 지울 수 없다.
    문장 끝에 검사하므로 공간 삭제의 연쇄 삭제는 막지 않는다."""
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def _json(name: str, default: str = "'{}'::jsonb") -> sa.Column:
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        # 지원을 만들 때 확정돼 있던 공고 버전. 제출본은 제출 시점 버전을 따로 고정한다.
        sa.Column("posting_version_id", sa.Uuid()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_note", sa.Text()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(
            "status in ('draft', 'submitted', 'passed', 'rejected', 'withdrawn')",
            name="ck_applications_status",
        ),
        sa.CheckConstraint(
            "status not in ('submitted', 'passed', 'rejected') or submitted_at is not null",
            name="ck_applications_submitted",
        ),
        _fk("posting_id", "job_postings", None),
        _fk("posting_version_id", "job_posting_versions", None),
        schema=S,
    )
    op.create_index("ix_applications_posting", "applications", ["posting_id"], schema=S)
    op.create_table(
        "application_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        # 공고 문항에서 만든 문서: 몇 번째 문항인지와 그때의 문항 문구(복사본).
        sa.Column("question_index", sa.Integer()),
        sa.Column("question_prompt", sa.Text()),
        sa.Column("char_limit", sa.Integer()),
        sa.Column("byte_limit", sa.Integer()),
        # {spaces: include|exclude, newlines: one|two|none, encoding: utf-8|cp949|euc-kr}
        _json("count_rule"),
        # 최신 revision 번호. 0이면 아직 저장한 본문이 없다. 저장할 때 base_revision과 비교한다.
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("application_id", "question_index"),
        sa.CheckConstraint(
            "kind in ('question', 'resume', 'cover_letter', 'portfolio', 'other')",
            name="ck_application_documents_kind",
        ),
        sa.CheckConstraint(
            "kind <> 'question' or question_prompt is not null",
            name="ck_application_documents_question",
        ),
        sa.CheckConstraint(
            "(char_limit is null or char_limit > 0) and (byte_limit is null or byte_limit > 0)",
            name="ck_application_documents_limits",
        ),
        sa.CheckConstraint("current_revision >= 0", name="ck_application_documents_revision"),
        _fk("application_id", "applications"),
        schema=S,
    )
    op.create_index(
        "ix_application_documents_application",
        "application_documents",
        ["application_id"],
        schema=S,
    )
    op.create_table(
        "document_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        # [{id, text, origin: user|ai, claim_ids: [...]}] — 문단마다 안정적인 ID
        sa.Column("blocks", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        # 저장 시점의 문서 설정으로 코드가 센 값 (참고용 스냅샷)
        _json("counts"),
        sa.Column("origin", sa.Text(), nullable=False),  # user | ai_suggestion
        sa.Column("suggestion_id", sa.Uuid()),
        sa.Column("reason", sa.Text()),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("document_id", "revision_no"),
        sa.UniqueConstraint("document_id", "idempotency_key"),
        sa.CheckConstraint("revision_no >= 1", name="ck_document_revisions_no"),
        sa.CheckConstraint(
            "origin in ('user', 'ai_suggestion')", name="ck_document_revisions_origin"
        ),
        sa.CheckConstraint(
            "(origin = 'ai_suggestion') = (suggestion_id is not null)",
            name="ck_document_revisions_suggestion",
        ),
        _fk("document_id", "application_documents"),
        schema=S,
    )
    # 문단 ↔ 브레인 주장. 근거는 확인된 주장만 가리킨다(코드에서 확인 상태를 본다).
    # AI 수정안은 여기에 들어갈 수 없다: claim_id는 core.claims만 가리킨다.
    op.create_table(
        "document_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Text(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("revision_id", "block_id", "claim_id"),
        _fk("revision_id", "document_revisions"),
        _fk("claim_id", "claims", None),
        schema=S,
    )
    op.create_index("ix_document_claims_claim", "document_claims", ["claim_id"], schema=S)
    op.create_table(
        "document_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("base_revision_id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("suggested_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        # 코드가 거부한 이유: [{code: number|role|organization|new_term|ats_manipulation, values}]
        _json("reject_reasons", "'[]'::jsonb"),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("applied_revision_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(
            "status in ('proposed', 'rejected', 'applied', 'dismissed')",
            name="ck_document_suggestions_status",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' or jsonb_array_length(reject_reasons) > 0",
            name="ck_document_suggestions_rejected",
        ),
        sa.CheckConstraint(
            "(status = 'applied') = (applied_revision_id is not null)",
            name="ck_document_suggestions_applied",
        ),
        _fk("document_id", "application_documents"),
        _fk("base_revision_id", "document_revisions"),
        _fk("applied_revision_id", "document_revisions", None),
        schema=S,
    )
    op.create_index(
        "ix_document_suggestions_document", "document_suggestions", ["document_id"], schema=S
    )
    op.create_foreign_key(
        "fk_document_revisions_suggestion",
        "document_revisions",
        "document_suggestions",
        ["workspace_id", "suggestion_id"],
        ["workspace_id", "id"],
        source_schema=S,
        referent_schema=S,
    )
    # 실제 제출본. 문서 본문·근거 주장 문장·공고 항목을 복사해 둔다(이후 수정이 소급되지 않게).
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("posting_version_id", sa.Uuid()),
        sa.Column("posting", JSONB(), nullable=False),
        sa.Column("documents", JSONB(), nullable=False),
        sa.Column("checks", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        _ts("submitted_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("application_id"),
        _fk("application_id", "applications", None),
        _fk("posting_id", "job_postings", None),
        _fk("posting_version_id", "job_posting_versions", None),
        schema=S,
    )
    tables = (
        "applications",
        "application_documents",
        "document_revisions",
        "document_claims",
        "document_suggestions",
        "application_submissions",
    )
    for table in tables:
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "applications"))
    op.execute(rls.grant_app_sql(S, "application_documents"))
    # 수정안은 상태만 바뀐다 (적용·닫기). 지우지 않는다.
    op.execute(rls.grant_app_sql(S, "document_suggestions", "SELECT, INSERT, UPDATE"))
    # 저장한 revision·근거 연결·제출본은 고치지 않는다.
    for table in ("document_revisions", "document_claims", "application_submissions"):
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))


def downgrade() -> None:
    op.drop_constraint("fk_document_revisions_suggestion", "document_revisions", schema=S)
    for table in (
        "application_submissions",
        "document_suggestions",
        "document_claims",
        "document_revisions",
        "application_documents",
        "applications",
    ):
        op.drop_table(table, schema=S)
