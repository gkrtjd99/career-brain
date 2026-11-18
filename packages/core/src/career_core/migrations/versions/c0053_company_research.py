"""core: 회사 정보 알아서 찾기 — 공고를 저장하면 그 회사의 공개 자료를 몇 쪽 가져와 항목 후보를 만든다 (2026-11-18)

Revision ID: c0053
Revises: c0052

- ``core.companies.research_status``·``researched_at``·``research_notes``·``research_requested_by``:
  마지막 "회사 정보 찾기"의 상태(``pending``·``done``·``failed``)와 시각, 무엇을 찾았는지·못 찾았는지 메모,
  요청한 사람(모델 호출 권한은 그 사람 것으로 본다).
- ``core.company_pages.origin``에 ``found``(찾기가 고른 공개 페이지: 회사 홈페이지의 소개·인재상·문화 쪽,
  위키백과)와 ``posting``(이 회사 공고의 글 — 인재상·회사 소개 문단에서 항목을 뽑는다). 둘 다 주소가 있다.
- 공고 항목(``job_postings.fields``)의 새 목록 ``talent``·``benefits``·``conditions``는 JSONB 안이라 DDL이 없다.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0053"
down_revision = "c0052"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.add_column("companies", sa.Column("research_status", sa.Text(), nullable=True), schema=S)
    op.add_column(
        "companies",
        sa.Column("researched_at", sa.DateTime(timezone=True), nullable=True),
        schema=S,
    )
    op.add_column(
        "companies",
        sa.Column("research_notes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema=S,
    )
    op.add_column(
        "companies", sa.Column("research_requested_by", sa.Uuid(), nullable=True), schema=S
    )
    op.create_check_constraint(
        "ck_companies_research_status",
        "companies",
        "research_status IS NULL OR research_status IN ('pending', 'done', 'failed')",
        schema=S,
    )
    op.drop_constraint("ck_company_pages_origin", "company_pages", schema=S)
    op.create_check_constraint(
        "ck_company_pages_origin",
        "company_pages",
        "origin IN ('url', 'paste', 'found', 'posting')",
        schema=S,
    )
    op.drop_constraint("ck_company_pages_url", "company_pages", schema=S)
    op.create_check_constraint(
        "ck_company_pages_url",
        "company_pages",
        "origin = 'paste' OR (url IS NOT NULL AND length(url) <= 2000)",
        schema=S,
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {S}.company_pages WHERE origin IN ('found', 'posting')")
    op.drop_constraint("ck_company_pages_url", "company_pages", schema=S)
    op.create_check_constraint(
        "ck_company_pages_url",
        "company_pages",
        "origin <> 'url' OR (url IS NOT NULL AND length(url) <= 2000)",
        schema=S,
    )
    op.drop_constraint("ck_company_pages_origin", "company_pages", schema=S)
    op.create_check_constraint(
        "ck_company_pages_origin", "company_pages", "origin IN ('url', 'paste')", schema=S
    )
    op.drop_constraint("ck_companies_research_status", "companies", schema=S)
    for col in ("research_requested_by", "research_notes", "researched_at", "research_status"):
        op.drop_column("companies", col, schema=S)
