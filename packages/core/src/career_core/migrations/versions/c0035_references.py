"""core: 참고 자료 보관함 — 다른 사람의 자기소개서·이력서·면접 후기·직무 글 (O14, plan §19.2)

Revision ID: c0035
Revises: c0034

내 경험(``claims``)과 다른 칸이다. 참고 글은 주장이 되지 않고 문서 근거(``document_claims``)로 이을 수 없다
(``document_claims.claim_id``는 ``claims``만 가리킨다). 근거 없는 문장 검사에서도 근거로 치지 않는다.

- ``core.reference_items``: 참고 글 하나. 종류(``accepted_essay`` 합격 자기소개서 · ``essay`` 다른 사람
  자기소개서 · ``resume`` 다른 사람 이력서 · ``interview_review`` 면접 후기 · ``job_article`` 직무 글 · ``other``)·
  제목·회사(선택, 회사를 지우면 연결만 비운다)·직무·연도·출처 메모·자료 등급·글(``text``: 개인정보를 가리기로
  했으면 가린 글)·원본(``sources`` 한 건, 용도 ``reference``. 붙여넣은 글도 참고 원본으로 저장한다).
- ``core.reference_notes``: 구조 메모. 문항·문단 구성·소재 종류·강조 역량. 모델 후보(``candidate``)를 사람이
  확인(``confirmed``)한다. 뒤의 기능(지원 준비·초안)은 원문 문장이 아니라 확인된 메모만 쓴다 (§19 원칙 2).
- 모두 FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.

c0034는 다른 작업이 쓰고 있다. 합칠 때 ``down_revision``을 다시 잇는다 (이 파일은 다른 표에 기대지 않는다).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0035"
down_revision = "c0034"
branch_labels = None
depends_on = None

S = "core"
KINDS = ("accepted_essay", "essay", "resume", "interview_review", "job_article", "other")
DATA_CLASSES = ("public", "private", "restricted")


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


def _short_list(column: str) -> str:
    """메모 칸(문단 구성·소재·역량): 20개 이하, NULL 원소 없음. 원소 길이(200자)는 usecase가 본다."""
    return f"cardinality({column}) <= 20 AND array_position({column}, NULL) IS NULL"


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    classes = ", ".join(f"'{c}'" for c in DATA_CLASSES)
    op.create_table(
        "reference_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("data_class", sa.Text(), nullable=False, server_default="private"),
        sa.Column("text", sa.Text(), nullable=False),
        # 저장할 때 개인정보를 가렸는지, 가린 곳 수 (가린 값 자체는 남기지 않는다)
        sa.Column("pii_masked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_reference_items_kind"),
        sa.CheckConstraint("length(title) between 1 and 300", name="ck_reference_items_title"),
        sa.CheckConstraint(
            "role IS NULL OR length(role) between 1 and 100", name="ck_reference_items_role"
        ),
        sa.CheckConstraint(
            "year IS NULL OR year between 1950 and 2100", name="ck_reference_items_year"
        ),
        sa.CheckConstraint(
            "source_note IS NULL OR length(source_note) <= 1000",
            name="ck_reference_items_source_note",
        ),
        sa.CheckConstraint(f"data_class IN ({classes})", name="ck_reference_items_data_class"),
        sa.CheckConstraint(
            "length(text) between 1 and 200000", name="ck_reference_items_text_size"
        ),
        sa.CheckConstraint("pii_masked >= 0", name="ck_reference_items_pii_masked"),
        schema=S,
    )
    # 회사·원본을 지우면 연결만 비운다 (workspace_id는 그대로: 칸을 지정한 SET NULL, c0033과 같은 방식)
    op.execute(
        f'ALTER TABLE "{S}".reference_items ADD CONSTRAINT fk_reference_items_company '
        f'FOREIGN KEY (workspace_id, company_id) REFERENCES "{S}".companies (workspace_id, id) '
        "ON DELETE SET NULL (company_id)"
    )
    op.execute(
        f'ALTER TABLE "{S}".reference_items ADD CONSTRAINT fk_reference_items_source '
        f'FOREIGN KEY (workspace_id, source_id) REFERENCES "{S}".sources (workspace_id, id) '
        "ON DELETE SET NULL (source_id)"
    )
    op.create_index(
        "ix_reference_items_kind", "reference_items", ["workspace_id", "kind"], schema=S
    )
    op.create_index(
        "ix_reference_items_company",
        "reference_items",
        ["workspace_id", "company_id"],
        schema=S,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.create_index(
        "ix_reference_items_source",
        "reference_items",
        ["workspace_id", "source_id"],
        schema=S,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_table(
        "reference_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column(
            "structure", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "materials", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "competencies",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "reference_id"],
            [f"{S}.reference_items.workspace_id", f"{S}.reference_items.id"],
            ondelete="CASCADE",
            name="fk_reference_notes_reference",
        ),
        sa.CheckConstraint(
            "question IS NULL OR length(question) between 1 and 1000",
            name="ck_reference_notes_question",
        ),
        sa.CheckConstraint(_short_list("structure"), name="ck_reference_notes_structure"),
        sa.CheckConstraint(_short_list("materials"), name="ck_reference_notes_materials"),
        sa.CheckConstraint(_short_list("competencies"), name="ck_reference_notes_competencies"),
        # 빈 메모는 없다
        sa.CheckConstraint(
            "question IS NOT NULL OR cardinality(structure) > 0 OR cardinality(materials) > 0 "
            "OR cardinality(competencies) > 0",
            name="ck_reference_notes_not_empty",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'confirmed')", name="ck_reference_notes_status"
        ),
        sa.CheckConstraint(
            "status <> 'confirmed' OR confirmed_at IS NOT NULL",
            name="ck_reference_notes_confirmed",
        ),
        sa.CheckConstraint("origin IN ('model', 'user')", name="ck_reference_notes_origin"),
        schema=S,
    )
    op.create_index(
        "ix_reference_notes_reference",
        "reference_notes",
        ["workspace_id", "reference_id"],
        schema=S,
    )
    for table in ("reference_items", "reference_notes"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("reference_notes", schema=S)
    op.drop_table("reference_items", schema=S)
