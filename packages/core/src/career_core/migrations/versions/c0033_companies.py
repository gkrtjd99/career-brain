"""core: 회사 조사 — 참고 지식 칸의 회사·원문·항목 (O13, plan §19.1)

Revision ID: c0033
Revises: c0031

내 경험(``claims``)과 다른 칸이다. 회사 항목은 주장이 되지 않고 문서 근거(``document_claims``)로 이을 수 없다
(``document_claims.claim_id``는 ``claims``만 가리킨다).

- ``core.companies``: 이름·별칭·홈페이지·선택 식별자(OpenDART ``corp_code`` 8자리, 사업자등록번호 10자리).
  ``name_key``(정규화한 이름, usecase가 만든다)는 공간 안에서 유일하다.
- ``core.company_pages``: 항목의 원문. 사용자가 준 페이지 한 건(``origin = 'url'``, 공고와 같은 가져오기 경로·
  worker 작업 ``company_fetch``) 또는 붙여넣은 글(``paste``). ``fetched_at`` = 글을 받은 시각.
  원본(``sources``)을 쓰지 않는다: 원본은 모두 경험 추출 대상이라 남의 자료가 섞이면 안 된다 (§19 원칙 1).
- ``core.company_facts``: 항목 하나. 종류·본문·인용(원문 ``company_pages.text`` 안의 위치와 그대로 복사한 글)·
  출처 주소와 가져온 시각(원문에서 복사)·기준일(``as_of`` + 정밀도 year/month/day, 모르면 비움)·확인 상태.
  인용 없는 항목은 없다 (``page_id``·``quote`` NOT NULL). 재무(``finance``)는 기준일이 있어야 한다.
- ``core.job_postings.company_id``: 공고 → 회사 (같은 공간, 회사를 지우면 연결만 비운다).
- 모두 FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "c0033"
down_revision = "c0031"
branch_labels = None
depends_on = None

S = "core"
FACT_KINDS = (
    "talent",
    "values",
    "business",
    "product",
    "finance",
    "culture",
    "news",
    "hiring",
    "other",
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


def _company_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "company_id"],
        [f"{S}.companies.workspace_id", f"{S}.companies.id"],
        ondelete="CASCADE",
        name=name,
    )


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column(
            "aliases", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column("homepage", sa.Text(), nullable=True),
        sa.Column("corp_code", sa.Text(), nullable=True),
        sa.Column("business_no", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "name_key", name="uq_companies_name_key"),
        sa.CheckConstraint("length(name) between 1 and 200", name="ck_companies_name"),
        sa.CheckConstraint("length(name_key) between 1 and 200", name="ck_companies_name_key"),
        sa.CheckConstraint(
            "cardinality(aliases) <= 20 AND array_position(aliases, NULL) IS NULL",
            name="ck_companies_aliases",
        ),
        sa.CheckConstraint(
            "homepage IS NULL OR (length(homepage) <= 500 AND homepage ~ '^https?://')",
            name="ck_companies_homepage",
        ),
        sa.CheckConstraint(
            "corp_code IS NULL OR corp_code ~ '^[0-9]{8}$'", name="ck_companies_corp_code"
        ),
        sa.CheckConstraint(
            "business_no IS NULL OR business_no ~ '^[0-9]{10}$'", name="ck_companies_business_no"
        ),
        schema=S,
    )
    op.create_table(
        "company_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("method", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        _company_fk("fk_company_pages_company"),
        sa.CheckConstraint("origin IN ('url', 'paste')", name="ck_company_pages_origin"),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed')", name="ck_company_pages_status"
        ),
        sa.CheckConstraint(
            "origin <> 'url' OR (url IS NOT NULL AND length(url) <= 2000)",
            name="ck_company_pages_url",
        ),
        sa.CheckConstraint(
            "status <> 'done' OR (text IS NOT NULL AND fetched_at IS NOT NULL)",
            name="ck_company_pages_done",
        ),
        sa.CheckConstraint(
            "text IS NULL OR length(text) <= 200000", name="ck_company_pages_text_size"
        ),
        sa.CheckConstraint("title IS NULL OR length(title) <= 300", name="ck_company_pages_title"),
        sa.CheckConstraint("candidates >= 0", name="ck_company_pages_candidates"),
        schema=S,
    )
    op.create_index(
        "ix_company_pages_company", "company_pages", ["workspace_id", "company_id"], schema=S
    )
    kinds = ", ".join(f"'{k}'" for k in FACT_KINDS)
    op.create_table(
        "company_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("as_of_precision", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_times(),
        sa.UniqueConstraint("workspace_id", "id"),
        _company_fk("fk_company_facts_company"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "page_id"],
            [f"{S}.company_pages.workspace_id", f"{S}.company_pages.id"],
            ondelete="CASCADE",
            name="fk_company_facts_page",
        ),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_company_facts_kind"),
        sa.CheckConstraint("length(body) between 1 and 2000", name="ck_company_facts_body"),
        sa.CheckConstraint("length(quote) between 1 and 2000", name="ck_company_facts_quote"),
        sa.CheckConstraint(
            "quote_start >= 0 AND quote_end > quote_start", name="ck_company_facts_quote_span"
        ),
        # 기준일과 정밀도는 같이 있거나 같이 없다. 재무는 기준일이 있어야 한다 (추측하지 않으므로 없으면 거부)
        sa.CheckConstraint(
            "(as_of IS NULL) = (as_of_precision IS NULL) "
            "AND (as_of_precision IS NULL OR as_of_precision IN ('year', 'month', 'day'))",
            name="ck_company_facts_as_of",
        ),
        sa.CheckConstraint(
            "kind <> 'finance' OR as_of IS NOT NULL", name="ck_company_facts_finance_as_of"
        ),
        sa.CheckConstraint("status IN ('candidate', 'confirmed')", name="ck_company_facts_status"),
        sa.CheckConstraint(
            "status <> 'confirmed' OR confirmed_at IS NOT NULL", name="ck_company_facts_confirmed"
        ),
        sa.CheckConstraint("origin IN ('model', 'user')", name="ck_company_facts_origin"),
        schema=S,
    )
    op.create_index(
        "ix_company_facts_company", "company_facts", ["workspace_id", "company_id"], schema=S
    )
    op.add_column("job_postings", sa.Column("company_id", sa.Uuid(), nullable=True), schema=S)
    # 회사를 지우면 공고의 연결만 비운다 (workspace_id는 그대로: 칸을 지정한 SET NULL, c0031과 같은 방식)
    op.execute(
        f'ALTER TABLE "{S}".job_postings ADD CONSTRAINT fk_job_postings_company '
        f'FOREIGN KEY (workspace_id, company_id) REFERENCES "{S}".companies (workspace_id, id) '
        "ON DELETE SET NULL (company_id)"
    )
    op.create_index(
        "ix_job_postings_company",
        "job_postings",
        ["workspace_id", "company_id"],
        schema=S,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    for table in ("companies", "company_pages", "company_facts"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_index("ix_job_postings_company", "job_postings", schema=S)
    op.drop_constraint("fk_job_postings_company", "job_postings", schema=S)
    op.drop_column("job_postings", "company_id", schema=S)
    op.drop_table("company_facts", schema=S)
    op.drop_table("company_pages", schema=S)
    op.drop_table("companies", schema=S)
