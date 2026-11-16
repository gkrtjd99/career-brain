"""core: study (requirements, items, sessions, assessments, attempts, mistakes, reviews)

Revision ID: c0009
Revises: c0006
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0009"
down_revision = "c0007"
branch_labels = None
depends_on = None

S = "core"

FIELDS = ("requirements", "preferred", "process")
ITEM_KINDS = ("skill", "topic", "other")
ITEM_STATUSES = ("planned", "in_progress", "done", "dropped")
ASSESSMENT_KINDS = (
    "coding_test",
    "written_test",
    "assignment",
    "interview",
    "certification",
    "other",
)
ASSESSMENT_STATUSES = ("planned", "done", "cancelled")
RESULTS = ("passed", "failed", "unknown")
REASONS = ("concept", "careless", "time", "misread", "unknown")
GRADES = ("again", "hard", "good", "easy")


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


def _in(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} in ({listed})", name=name)


def _fk_set_null(table: str, column: str, parent: str) -> None:
    """선택 연결. 부모가 지워지면 이 칸만 비운다 (workspace_id는 그대로, PostgreSQL 15+)."""
    op.execute(
        f'ALTER TABLE "{S}"."{table}" ADD CONSTRAINT "fk_{table}_{column}" '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}"."{parent}" (workspace_id, id) '
        f"ON DELETE SET NULL ({column})"
    )


def upgrade() -> None:
    # 저장 공고의 요건 한 줄 (계획 문서의 assessment_requirements). 확정 당시 문장 그대로, 불변.
    op.create_table(
        "study_requirements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "posting_id", "field", "text"),
        _in("field", FIELDS, "ck_study_requirements_field"),
        sa.CheckConstraint("length(btrim(text)) > 0", name="ck_study_requirements_text"),
        _fk("posting_id", "job_postings"),
        schema=S,
    )
    # 요건 ↔ 주장. 사용자가 직접 연결한다 (자동 판정으로 만들지 않는다).
    op.create_table(
        "study_requirement_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("requirement_id", "claim_id"),
        _fk("requirement_id", "study_requirements"),
        _fk("claim_id", "claims"),
        schema=S,
    )
    op.create_table(
        "study_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        # 공고 전형 절차 한 줄에서 만든 경우 그 요건 행
        sa.Column("requirement_id", sa.Uuid()),
        # 일정이 공고에 없으면 비운다 (추측하지 않음)
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("kind", ASSESSMENT_KINDS, "ck_study_assessments_kind"),
        _in("status", ASSESSMENT_STATUSES, "ck_study_assessments_status"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_study_assessments_title"),
        schema=S,
    )
    _fk_set_null("study_assessments", "requirement_id", "study_requirements")
    op.create_table(
        "study_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requirement_id", sa.Uuid()),
        sa.Column("assessment_id", sa.Uuid()),
        sa.Column("notes", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("kind", ITEM_KINDS, "ck_study_items_kind"),
        _in("status", ITEM_STATUSES, "ck_study_items_status"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_study_items_title"),
        sa.CheckConstraint(
            "(status = 'done') = (completed_at is not null)", name="ck_study_items_completed"
        ),
        schema=S,
    )
    _fk_set_null("study_items", "requirement_id", "study_requirements")
    _fk_set_null("study_items", "assessment_id", "study_assessments")
    op.create_index("ix_study_items_requirement", "study_items", ["requirement_id"], schema=S)
    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("study_item_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text()),
        # [{"title": str, "url": str | None}] — 사용자가 적은 자료. 가져오지 않는다.
        sa.Column("resources", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("minutes > 0 and minutes <= 1440", name="ck_study_sessions_minutes"),
        _fk("study_item_id", "study_items"),
        schema=S,
    )
    op.create_index("ix_study_sessions_item", "study_sessions", ["study_item_id"], schema=S)
    # 시험·연습 응시 1회. 결과 기록이므로 고치지 않는다 (SELECT/INSERT만).
    op.create_table(
        "study_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("assessment_id", sa.Uuid()),
        sa.Column("study_item_id", sa.Uuid()),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("score", sa.Numeric(10, 2)),
        sa.Column("max_score", sa.Numeric(10, 2)),
        sa.Column("minutes", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("result", RESULTS, "ck_study_attempts_result"),
        sa.CheckConstraint(
            "num_nonnulls(assessment_id, study_item_id) >= 1", name="ck_study_attempts_target"
        ),
        sa.CheckConstraint(
            "(score is null or score >= 0) and (max_score is null or max_score > 0) "
            "and (score is null or max_score is null or score <= max_score)",
            name="ck_study_attempts_score",
        ),
        sa.CheckConstraint(
            "minutes is null or (minutes > 0 and minutes <= 1440)",
            name="ck_study_attempts_minutes",
        ),
        _fk("assessment_id", "study_assessments"),
        _fk("study_item_id", "study_items"),
        schema=S,
    )
    # 오답 + 복습 상태. 오답은 경험이 아니다.
    op.create_table(
        "study_mistakes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("attempt_id", sa.Uuid()),
        sa.Column("study_item_id", sa.Uuid()),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("wrong_answer", sa.Text()),
        sa.Column("correct_answer", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_note", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ease", sa.Integer(), nullable=False, server_default="2500"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("reason", REASONS, "ck_study_mistakes_reason"),
        _in("status", ("open", "resolved"), "ck_study_mistakes_status"),
        sa.CheckConstraint(
            "num_nonnulls(attempt_id, study_item_id) >= 1", name="ck_study_mistakes_target"
        ),
        sa.CheckConstraint("length(btrim(question)) > 0", name="ck_study_mistakes_question"),
        sa.CheckConstraint(
            "repetitions >= 0 and interval_days >= 0 and ease >= 1300",
            name="ck_study_mistakes_schedule",
        ),
        _fk("attempt_id", "study_attempts"),
        _fk("study_item_id", "study_items"),
        schema=S,
    )
    op.create_index(
        "ix_study_mistakes_due",
        "study_mistakes",
        ["workspace_id", "due_at"],
        schema=S,
        postgresql_where=sa.text("status = 'open'"),
    )
    # 복습 기록 (불변). 일정 계산의 입력·출력을 남겨 다시 계산할 수 있게 한다.
    op.create_table(
        "study_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("mistake_id", sa.Uuid(), nullable=False),
        sa.Column("grade", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_before", sa.Integer(), nullable=False),
        sa.Column("interval_after", sa.Integer(), nullable=False),
        sa.Column("ease_after", sa.Integer(), nullable=False),
        sa.Column("due_at_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        _in("grade", GRADES, "ck_study_reviews_grade"),
        _fk("mistake_id", "study_mistakes"),
        schema=S,
    )
    # 학습 결과 → 경험 후보 주장. 사용자가 직접 올린 경우에만 생긴다 (주장은 extracted로 시작).
    op.create_table(
        "study_claim_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("study_item_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid()),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("claim_id"),
        _fk("study_item_id", "study_items"),
        _fk("claim_id", "claims"),
        schema=S,
    )
    _fk_set_null("study_claim_candidates", "attempt_id", "study_attempts")

    tables = (
        "study_requirements",
        "study_requirement_claims",
        "study_assessments",
        "study_items",
        "study_sessions",
        "study_attempts",
        "study_mistakes",
        "study_reviews",
        "study_claim_candidates",
    )
    for table in tables:
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    for table in ("study_assessments", "study_items", "study_sessions", "study_mistakes"):
        op.execute(rls.grant_app_sql(S, table))
    # 확정 당시 요건 문장·응시 결과·복습 기록·후보 출처는 고치지 않는다.
    for table in (
        "study_requirements",
        "study_attempts",
        "study_reviews",
        "study_claim_candidates",
    ):
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))
    # 요건 ↔ 주장 연결은 사용자가 끊을 수 있다.
    op.execute(rls.grant_app_sql(S, "study_requirement_claims", "SELECT, INSERT, DELETE"))


def downgrade() -> None:
    for table in (
        "study_claim_candidates",
        "study_reviews",
        "study_mistakes",
        "study_attempts",
        "study_sessions",
        "study_items",
        "study_assessments",
        "study_requirement_claims",
        "study_requirements",
    ):
        op.drop_table(table, schema=S)
