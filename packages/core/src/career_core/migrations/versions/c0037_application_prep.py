"""core: 공고별 지원 준비 문서 — 문항마다 쓸 경험 후보·요건 충족표 (O15, plan §19.3)

Revision ID: c0037
Revises: c0036

- ``core.application_preps``: 지원 하나에 하나(``UNIQUE (workspace_id, application_id)``). 다시 만들면 같은 행을
  고친다. 내용은 만들 때의 공고 버전·쓴 경험의 내용 해시(``basis``)에 묶인 산출물이다: 공고가 다시 저장되거나
  쓴 경험이 바뀌면 읽을 때 "오래됨"으로 보인다(저장 칸이 아니라 ``basis``와 지금 값의 비교).
  - ``questions``: 문항마다 후보 경험(원문 근거·이유·빈 곳)·회사 쪽 연결·참고 구성(구조 메모, 문장 없음).
  - ``requirements``: 요건 충족표 (근거 있음 / 약함 / 없음).
  - ``choices``: 사람이 고른 경험 ``{document_id: [{experience_id, claim_ids, chosen_at}]}``. 다시 만들어도 남는다.
    고른다고 문서에 글이 생기지 않는다(본문은 사람이 쓴다, §19 원칙 5).
  - ``mode``: ``model``(모델이 후보 안에서 고르고 이유를 씀) | ``search``(모델 없이 검색 순위).
- 지원을 지우면 함께 지워진다. FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.

c0036 뒤에 다른 작업의 migration이 더 있을 수 있다. 합칠 때 ``down_revision``을 다시 잇는다
(이 파일은 c0007의 ``applications``·``job_postings`` 말고 다른 표에 기대지 않는다).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0037"
down_revision = "c0036"
branch_labels = None
depends_on = None

S = "core"
TABLE = "application_preps"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("posting_version_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("basis", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("questions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("requirements", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("company", JSONB(), nullable=True),
        sa.Column("choices", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "application_id", name="uq_application_preps_app"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            [f"{S}.applications.workspace_id", f"{S}.applications.id"],
            ondelete="CASCADE",
            name="fk_application_preps_application",
        ),
        sa.CheckConstraint("mode IN ('model', 'search')", name="ck_application_preps_mode"),
        sa.CheckConstraint(
            "jsonb_typeof(questions) = 'array' AND jsonb_typeof(requirements) = 'array' "
            "AND jsonb_typeof(choices) = 'object' AND jsonb_typeof(basis) = 'object' "
            "AND jsonb_typeof(notes) = 'array'",
            name="ck_application_preps_shape",
        ),
        sa.CheckConstraint(
            "pg_column_size(questions) + pg_column_size(requirements) <= 2000000",
            name="ck_application_preps_size",
        ),
        schema=S,
    )
    for stmt in rls.workspace_isolation_sql(S, TABLE):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, TABLE, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table(TABLE, schema=S)
