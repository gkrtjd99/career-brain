"""core: 보고 있는 페이지 보내기 — 내 브라우저에서 보낸 공고·자료 (plan §8 "내 브라우저에서 보내기", §9)

Revision ID: c0043
Revises: c0042

- ``core.job_posting_versions.method``에 ``browser_capture``를 더한다: 사용자가 자기 브라우저에서 보고 있는
  공고 페이지를 북마크로 보내 만든 버전 (서버는 그 주소를 가져오지 않았다).
- ``core.sources.origin_url``: 브라우저에서 보낸 글(참고 자료·내 경험 자료)의 원래 주소. 파일로 올린 원본은
  null. 2,000자 이하, http(s)만.
"""

import sqlalchemy as sa
from alembic import op

revision = "c0043"
down_revision = "c0042"
branch_labels = None
depends_on = None

S = "core"
OLD_METHODS = ("http", "browser", "manual_text")
NEW_METHODS = (*OLD_METHODS, "browser_capture")


def _methods(values: tuple[str, ...]) -> str:
    return "method in (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_job_posting_versions_method", "job_posting_versions", schema=S, type_="check"
    )
    op.create_check_constraint(
        "ck_job_posting_versions_method",
        "job_posting_versions",
        _methods(NEW_METHODS),
        schema=S,
    )
    op.add_column("sources", sa.Column("origin_url", sa.Text(), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_sources_origin_url",
        "sources",
        "origin_url IS NULL OR (length(origin_url) <= 2000 AND origin_url ~ '^https?://')",
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_origin_url", "sources", schema=S, type_="check")
    op.drop_column("sources", "origin_url", schema=S)
    # 버전은 고치지 않는 표다 (앱 role은 SELECT/INSERT만). 되돌리기는 관리자 연결로 돈다
    op.execute(
        f"UPDATE \"{S}\".job_posting_versions SET method = 'manual_text' "
        "WHERE method = 'browser_capture'"
    )
    op.drop_constraint(
        "ck_job_posting_versions_method", "job_posting_versions", schema=S, type_="check"
    )
    op.create_check_constraint(
        "ck_job_posting_versions_method",
        "job_posting_versions",
        _methods(OLD_METHODS),
        schema=S,
    )
