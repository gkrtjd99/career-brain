"""core: source_spans, experiences, extraction_runs, claims, claim_evidence, interviews

Revision ID: c0004
Revises: c0003
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0004"
down_revision = "c0003"
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


def _fk(column: str, parent: str, ondelete: str = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def _in(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} in ({listed})", name=name)


CLAIM_STATUSES = ("extracted", "self_confirmed", "evidence_supported", "disputed", "retired")
CLAIM_KINDS = ("responsibility", "action", "result", "decision", "difficulty", "skill", "other")
CONTRIBUTIONS = ("personal", "team", "unknown")
ORIGINS = ("ai_extraction", "interview", "user")
FOCUSES = (
    "context",
    "personal_contribution",
    "metric",
    "decision",
    "difficulty",
    "team_vs_personal",
)


def upgrade() -> None:
    op.create_table(
        "source_spans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("document_block_id", sa.Uuid(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "document_block_id", "start_char", "end_char"),
        sa.CheckConstraint(
            "start_char >= 0 and end_char > start_char and length(quote) = end_char - start_char",
            name="ck_source_spans_range",
        ),
        _fk("document_block_id", "document_blocks"),
        schema=S,
    )
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("parse_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("error_code", sa.Text()),
        sa.Column("blocks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocks_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("experiences_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claims_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claims_dropped", sa.Integer(), nullable=False, server_default="0"),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("status", ("pending", "complete", "partial", "failed"), "ck_extraction_runs_status"),
        _fk("parse_run_id", "parse_runs"),
        schema=S,
    )
    op.create_table(
        "experiences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("period_text", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("status", ("active", "retired"), "ck_experiences_status"),
        _in("origin", ORIGINS, "ck_experiences_origin"),
        _fk("extraction_run_id", "extraction_runs", "RESTRICT"),
        schema=S,
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("experience_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("contribution", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("status", CLAIM_STATUSES, "ck_claims_status"),
        _in("kind", CLAIM_KINDS, "ck_claims_kind"),
        _in("contribution", CONTRIBUTIONS, "ck_claims_contribution"),
        _in("origin", ORIGINS, "ck_claims_origin"),
        # 확정된 주장은 누가 언제 확인했는지와 팀/본인 구분이 있어야 한다.
        sa.CheckConstraint(
            "status not in ('self_confirmed', 'evidence_supported') or "
            "(confirmed_by is not null and confirmed_at is not null and contribution <> 'unknown')",
            name="ck_claims_confirmed",
        ),
        sa.CheckConstraint("length(btrim(statement)) > 0", name="ck_claims_statement"),
        _fk("experience_id", "experiences"),
        _fk("extraction_run_id", "extraction_runs", "RESTRICT"),
        schema=S,
    )
    op.create_index("ix_claims_experience", "claims", ["experience_id"], schema=S)
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("experience_id", sa.Uuid()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("status", ("open", "closed"), "ck_interview_sessions_status"),
        _fk("experience_id", "experiences"),
        schema=S,
    )
    op.create_table(
        "interview_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("model_call_id", sa.Uuid()),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "session_id", "ordinal"),
        _in("focus", FOCUSES, "ck_interview_questions_focus"),
        _in("origin", ("template", "ai"), "ck_interview_questions_origin"),
        _fk("session_id", "interview_sessions"),
        schema=S,
    )
    op.create_table(
        "interview_answers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("answered_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("length(btrim(answer)) > 0", name="ck_interview_answers_answer"),
        _fk("question_id", "interview_questions"),
        schema=S,
    )
    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_span_id", sa.Uuid()),
        sa.Column("interview_answer_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("claim_id", "source_span_id"),
        sa.UniqueConstraint("claim_id", "interview_answer_id"),
        # 근거는 사용자 원문 위치 또는 사용자가 쓴 인터뷰 답뿐이다. AI 텍스트를 가리킬 칸이 없다.
        sa.CheckConstraint(
            "(kind = 'source_span' and source_span_id is not null "
            "and interview_answer_id is null) or "
            "(kind = 'interview_answer' and interview_answer_id is not null "
            "and source_span_id is null)",
            name="ck_claim_evidence_kind",
        ),
        _fk("claim_id", "claims"),
        _fk("source_span_id", "source_spans", "RESTRICT"),
        _fk("interview_answer_id", "interview_answers", "RESTRICT"),
        schema=S,
    )
    op.create_index("ix_claim_evidence_claim", "claim_evidence", ["claim_id"], schema=S)

    mutable = (
        "extraction_runs",
        "experiences",
        "claims",
        "interview_sessions",
        "interview_questions",
        "claim_evidence",
    )
    immutable = ("source_spans", "interview_answers")  # 근거 원본: 추가·조회만
    for table in mutable + immutable:
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    for table in mutable:
        op.execute(rls.grant_app_sql(S, table))
    for table in immutable:
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))


def downgrade() -> None:
    for table in (
        "claim_evidence",
        "interview_answers",
        "interview_questions",
        "interview_sessions",
        "claims",
        "experiences",
        "extraction_runs",
        "source_spans",
    ):
        op.drop_table(table, schema=S)
