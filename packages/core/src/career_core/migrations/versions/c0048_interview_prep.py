"""core: 면접 준비 — 지원 하나의 예상 질문·출처·쓸 경험·사람이 쓴 답변 메모 (plan §19.4, O16)

Revision ID: c0048
Revises: c0047

- ``core.interview_preps``: 지원 하나에 하나(``UNIQUE (workspace_id, application_id)``). 만들 때의 공고·제출본·
  회사 항목·참고 메모·쓴 경험의 해시(``basis``)에 묶인 산출물이다. 읽을 때 지금 값과 비교해 "오래됨"을 보인다.
  ``submission_id``는 만들 때의 제출본(없을 수 있다). ``mode``: ``model``(모델이 질문을 다듬고 후보 안에서 고름) |
  ``search``(모델 없이 정해진 규칙 질문 + 검색 순위).
- ``core.interview_prep_questions``: 예상 질문 하나. 질문마다 출처가 있다(``source_kind`` + ``source`` jsonb):
  - ``posting``: 공고 확인 버전과 항목(자격 요건·우대·담당 업무, 몇 번째, 글).
  - ``submission``: 제출본 문장. 제출본·문서·revision·블록·글자 위치·인용 그대로. 그 제출본에 묶인다
    (``submission_id``, 제출본이 바뀌면 읽을 때 오래됨).
  - ``company``: 확인한 회사 항목(기준일·가져온 날). ``reference``: 참고 면접 후기의 확인한 구조 메모(문항·강조
    역량만, 남의 답 문장 없음 — 참고는 근거가 아니다). ``common``: 공통 질문(``common_kind`` = 공통 답변 종류).
    ``user``: 사람이 직접 더한 질문.
  ``source_key``는 출처 하나의 고정 키라 다시 만들 때 같은 행을 고친다(``UNIQUE (prep_id, source_key)``).
  ``suggestions``: 쓸 경험 후보(확인한 주장과 원문 근거). ``answer``: 사람이 쓴 답변 메모 — 앱이 만들지 않는다.
  ``answer_asset_id``: 이은 공통 답변(지우면 비워진다).
- 제출본·회사 항목·참고 메모·공통 답변 FK는 지워지면 칸만 비운다(SET NULL (칸)). 출처 jsonb에 그때 값이 남아 질문은
  출처를 잃지 않는다. 지원을 지우면 함께 지워진다.
- 두 표 모두 FORCE RLS, 앱 role ``SELECT, INSERT, UPDATE, DELETE``. 다른 공간 행은 복합 FK가 막는다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0048"
down_revision = "c0047"
branch_labels = None
depends_on = None

S = "core"
SOURCE_KINDS = ("posting", "submission", "company", "reference", "common", "user")
ORIGINS = ("rule", "model", "user")
COMMON_KINDS = ("intro", "motivation", "personality", "conflict", "failure", "aspiration", "other")


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


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "interview_preps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("basis", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "application_id", name="uq_interview_preps_app"),
        sa.CheckConstraint("mode IN ('model', 'search')", name="ck_interview_preps_mode"),
        sa.CheckConstraint(
            "jsonb_typeof(basis) = 'object' AND jsonb_typeof(notes) = 'array'",
            name="ck_interview_preps_shape",
        ),
        schema=S,
    )
    op.execute(
        _fk(
            "interview_preps",
            "fk_interview_preps_application",
            "application_id",
            "applications",
            "CASCADE",
        )
    )
    op.execute(
        _fk(
            "interview_preps",
            "fk_interview_preps_submission",
            "submission_id",
            "application_submissions",
            "SET NULL (submission_id)",
        )
    )

    op.create_table(
        "interview_prep_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("prep_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("company_fact_id", sa.Uuid(), nullable=True),
        sa.Column("reference_note_id", sa.Uuid(), nullable=True),
        sa.Column("common_kind", sa.Text(), nullable=True),
        sa.Column("suggestions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_asset_id", sa.Uuid(), nullable=True),
        sa.Column("answer_updated_by", sa.Uuid(), nullable=True),
        sa.Column("answer_updated_at", sa.DateTime(timezone=True), nullable=True),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("prep_id", "source_key", name="uq_interview_prep_questions_source"),
        sa.CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_interview_prep_q_kind"),
        sa.CheckConstraint(_in("origin", ORIGINS), name="ck_interview_prep_q_origin"),
        sa.CheckConstraint(
            "common_kind IS NULL OR " + _in("common_kind", COMMON_KINDS),
            name="ck_interview_prep_q_common_kind",
        ),
        sa.CheckConstraint(
            "length(question) between 1 and 500", name="ck_interview_prep_q_question"
        ),
        sa.CheckConstraint(
            "length(source_key) between 1 and 300", name="ck_interview_prep_q_source_key"
        ),
        sa.CheckConstraint("length(answer) <= 5000", name="ck_interview_prep_q_answer"),
        sa.CheckConstraint("position >= 0", name="ck_interview_prep_q_position"),
        # 질문마다 출처: 출처 jsonb의 kind가 source_kind와 같고, 종류마다 가리키는 것이 있다
        sa.CheckConstraint(
            "jsonb_typeof(source) = 'object' AND source->>'kind' = source_kind",
            name="ck_interview_prep_q_source",
        ),
        sa.CheckConstraint(
            "source_kind <> 'submission' OR (source ? 'submission_id' AND source ? 'revision_id' "
            "AND source ? 'quote' AND source ? 'start' AND source ? 'end')",
            name="ck_interview_prep_q_submission",
        ),
        sa.CheckConstraint(
            "source_kind <> 'posting' OR (source ? 'field' AND source ? 'text')",
            name="ck_interview_prep_q_posting",
        ),
        sa.CheckConstraint(
            "source_kind <> 'company' OR source ? 'fact_id'", name="ck_interview_prep_q_company"
        ),
        sa.CheckConstraint(
            "source_kind <> 'reference' OR source ? 'note_id'",
            name="ck_interview_prep_q_reference",
        ),
        sa.CheckConstraint(
            "source_kind <> 'common' OR common_kind IS NOT NULL",
            name="ck_interview_prep_q_common",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(suggestions) = 'array' AND pg_column_size(suggestions) <= 500000",
            name="ck_interview_prep_q_suggestions",
        ),
        schema=S,
    )
    fks = (
        ("fk_interview_prep_q_prep", "prep_id", "interview_preps", "CASCADE"),
        (
            "fk_interview_prep_q_submission",
            "submission_id",
            "application_submissions",
            "SET NULL (submission_id)",
        ),
        (
            "fk_interview_prep_q_fact",
            "company_fact_id",
            "company_facts",
            "SET NULL (company_fact_id)",
        ),
        (
            "fk_interview_prep_q_note",
            "reference_note_id",
            "reference_notes",
            "SET NULL (reference_note_id)",
        ),
        (
            "fk_interview_prep_q_answer_asset",
            "answer_asset_id",
            "answer_assets",
            "SET NULL (answer_asset_id)",
        ),
    )
    for name, column, parent, action in fks:
        op.execute(_fk("interview_prep_questions", name, column, parent, action))
    op.create_index(
        "ix_interview_prep_questions_prep",
        "interview_prep_questions",
        ["workspace_id", "prep_id", "position"],
        schema=S,
    )
    for table in ("interview_preps", "interview_prep_questions"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("interview_prep_questions", schema=S)
    op.drop_table("interview_preps", schema=S)
