"""core: 수정안·인터뷰 질문 요청 기록, 모델 장부의 실행 태그, 실행 비용의 장부 기준

Revision ID: c0016
Revises: c0015

- ``document_suggestion_requests``: AI 수정안 요청 1회 = 1행. 모델이 돌려준 질문 글(``questions``)과
  개수(제안·거부·버림)를 문서 쪽에 남긴다. 실행 기록(run position·event)에는 여전히 글을 넣지 않는다.
  멱등 키가 같은 요청은 이 행으로 다시 돌려준다 — 수정안이 0개였던 문단도 재개 때 모델을 다시 부르지 않는다.
- ``interview_question_requests`` + ``interview_questions.request_id``: AI 인터뷰 질문 요청 1회 = 1행.
  ``interview_sessions.idempotency_key``와 함께 일괄 workflow(``interview_question_batch``)의 재개 중복을 막는다.
- ``model_calls.run_id``·``run_step``: 실행 단계 안의 호출이면 어느 실행·단계인지. 단계 도중 worker가
  죽어도 장부에서 실행 비용을 다시 맞출 수 있다. 장부는 여전히 추가·조회만 (앱 role 권한 그대로).
- ``runs.cost_from_ledger``: 참이면 실행 비용(``cost_usd``·``cost_unknown``·``model_calls``)을 장부의
  태그 붙은 행에서 계산한다. 이 migration 전에 만든 실행은 거짓(태그 없는 호출이 있으므로 예전처럼 더한다).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0016"
down_revision = "c0015"
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
    return sa.ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{S}.{parent}.workspace_id", f"{S}.{parent}.id"],
        ondelete=ondelete,
    )


def _fk_set_null(table: str, column: str, parent: str) -> None:
    """선택 연결. 부모가 지워지면 이 칸만 비운다 (workspace_id는 그대로, PostgreSQL 15+)."""
    op.execute(
        f'ALTER TABLE "{S}"."{table}" ADD CONSTRAINT "fk_{table}_{column}" '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}"."{parent}" (workspace_id, id) '
        f"ON DELETE SET NULL ({column})"
    )


def _counts(*names: str) -> list[sa.Column]:
    return [sa.Column(n, sa.Integer(), nullable=False, server_default="0") for n in names]


def upgrade() -> None:
    # --- 모델 장부: 실행·단계 태그 (선택). 비용 보정 조회용 부분 색인.
    op.add_column("model_calls", sa.Column("run_id", sa.Uuid()), schema=S)
    op.add_column("model_calls", sa.Column("run_step", sa.Integer()), schema=S)
    op.create_check_constraint(
        "ck_model_calls_run_step", "model_calls", "run_step is null or run_step >= 0", schema=S
    )
    _fk_set_null("model_calls", "run_id", "runs")
    op.create_index(
        "ix_model_calls_run",
        "model_calls",
        ["workspace_id", "run_id"],
        schema=S,
        postgresql_where=sa.text("run_id is not null"),
    )

    # --- 실행 비용 기준. 지금 있는 실행은 거짓(예전 방식), 이후 앱이 만드는 실행은 참.
    op.add_column(
        "runs",
        sa.Column("cost_from_ledger", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )

    # --- 수정안 요청 기록: 모델이 되물은 질문 글과 개수. 멱등 키로 같은 요청을 다시 돌려준다.
    op.create_table(
        "document_suggestion_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("base_revision_id", sa.Uuid(), nullable=False),
        # 대상 문단 ID 목록 (문단 글은 넣지 않는다, 글은 수정안·revision에 있다)
        sa.Column("block_ids", JSONB(), nullable=False),
        # 모델이 사실 대신 물어본 질문 (사용자에게 그대로 보여 준다, 근거가 아니다)
        sa.Column("questions", JSONB(), nullable=False),
        *_counts("proposed", "rejected", "dropped"),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        # NULL 키는 겹쳐도 된다 (키 없는 요청)
        sa.UniqueConstraint(
            "document_id", "idempotency_key", name="uq_document_suggestion_requests_idempotency"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(block_ids) = 'array' and jsonb_typeof(questions) = 'array'",
            name="ck_document_suggestion_requests_arrays",
        ),
        sa.CheckConstraint(
            "proposed >= 0 and rejected >= 0 and dropped >= 0",
            name="ck_document_suggestion_requests_counts",
        ),
        _fk("document_id", "application_documents"),
        _fk("base_revision_id", "document_revisions"),
        schema=S,
    )
    _fk_set_null("document_suggestion_requests", "run_id", "runs")
    op.create_index(
        "ix_document_suggestion_requests_document",
        "document_suggestion_requests",
        ["document_id", "created_at"],
        schema=S,
    )

    # --- 인터뷰: 세션 멱등 키, 질문 요청 기록, 질문 → 요청
    op.add_column("interview_sessions", sa.Column("idempotency_key", sa.Text()), schema=S)
    op.create_unique_constraint(
        "uq_interview_sessions_idempotency",
        "interview_sessions",
        ["workspace_id", "idempotency_key"],
        schema=S,
    )
    op.create_table(
        "interview_question_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        *_counts("requested", "created"),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="uq_interview_question_requests_idempotency"
        ),
        sa.CheckConstraint(
            "requested >= 0 and created >= 0", name="ck_interview_question_requests_counts"
        ),
        _fk("session_id", "interview_sessions"),
        schema=S,
    )
    _fk_set_null("interview_question_requests", "run_id", "runs")
    op.add_column("interview_questions", sa.Column("request_id", sa.Uuid()), schema=S)
    op.create_foreign_key(
        "fk_interview_questions_request",
        "interview_questions",
        "interview_question_requests",
        ["workspace_id", "request_id"],
        ["workspace_id", "id"],
        source_schema=S,
        referent_schema=S,
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_interview_questions_request",
        "interview_questions",
        ["request_id"],
        schema=S,
        postgresql_where=sa.text("request_id is not null"),
    )

    for table in ("document_suggestion_requests", "interview_question_requests"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        # 요청 기록은 고치지 않는다 (추가·조회만)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))


def downgrade() -> None:
    op.drop_index("ix_interview_questions_request", "interview_questions", schema=S)
    op.drop_constraint(
        "fk_interview_questions_request", "interview_questions", schema=S, type_="foreignkey"
    )
    op.drop_column("interview_questions", "request_id", schema=S)
    op.drop_table("interview_question_requests", schema=S)
    op.drop_constraint(
        "uq_interview_sessions_idempotency", "interview_sessions", schema=S, type_="unique"
    )
    op.drop_column("interview_sessions", "idempotency_key", schema=S)
    op.drop_table("document_suggestion_requests", schema=S)
    op.drop_column("runs", "cost_from_ledger", schema=S)
    op.drop_index("ix_model_calls_run", "model_calls", schema=S)
    op.drop_constraint("fk_model_calls_run_id", "model_calls", schema=S, type_="foreignkey")
    op.drop_constraint("ck_model_calls_run_step", "model_calls", schema=S)
    op.drop_column("model_calls", "run_step", schema=S)
    op.drop_column("model_calls", "run_id", schema=S)
