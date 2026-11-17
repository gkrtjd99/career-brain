"""core: 학력 — 브레인 "나 › 학력" (plan §20, 한국 지원서의 학력 칸)

Revision ID: c0040
Revises: c0036

- ``core.education``: 학교 하나(고등학교·전문대·대학·대학원). 학교·캠퍼스(선택)·전공·복수/부전공·학위 과정
  (``high_school``·``associate``·``bachelor``·``master``·``doctor``)·상태(``enrolled`` 재학·``on_leave`` 휴학·
  ``expected`` 졸업예정·``graduated`` 졸업·``completed`` 수료·``dropped`` 중퇴)·편입 여부·입학/졸업 달(그 달 1일)·
  학점과 만점(4.5·4.3·4.0·100, 학점 ≤ 만점)·메모, 증빙(``source_id``, 용도 증빙 원본)·경험(``experience_id``) 연결.
- 자격·이수 기록(``credentials``)의 ``degree`` 종류는 그대로 읽히고, 화면에서 "학력으로 옮기기"로 이 표로 옮긴다.
- FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.

병렬 작업 중 번호: 이 갈래에서는 c0036 다음이다. 합칠 때 c0039 뒤로 잇는다 (c0040 → c0041 → c0042).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0040"
down_revision = "c0036"
branch_labels = None
depends_on = None

S = "core"
DEGREES = ("high_school", "associate", "bachelor", "master", "doctor")
STATUSES = ("enrolled", "on_leave", "expected", "graduated", "completed", "dropped")
EXTRA_MAJOR_KINDS = ("double", "minor")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _set_null_fk(name: str, column: str, table: str) -> str:
    return (
        f'ALTER TABLE "{S}".education ADD CONSTRAINT {name} '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}".{table} (workspace_id, id) '
        f"ON DELETE SET NULL ({column})"
    )


def upgrade() -> None:
    op.create_table(
        "education",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("school", sa.Text(), nullable=False),
        sa.Column("campus", sa.Text(), nullable=True),
        sa.Column("major", sa.Text(), nullable=True),
        sa.Column("extra_major", sa.Text(), nullable=True),
        sa.Column("extra_major_kind", sa.Text(), nullable=True),
        sa.Column("degree", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("transferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entered_on", sa.Date(), nullable=True),
        sa.Column("graduated_on", sa.Date(), nullable=True),
        sa.Column("gpa", sa.Numeric(5, 2), nullable=True),
        sa.Column("gpa_scale", sa.Numeric(5, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("experience_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(f"degree IN ({_in(DEGREES)})", name="ck_education_degree"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_education_status"),
        sa.CheckConstraint("length(school) between 1 and 100", name="ck_education_school"),
        sa.CheckConstraint(
            "(campus IS NULL OR length(campus) between 1 and 50) "
            "AND (major IS NULL OR length(major) between 1 and 100) "
            "AND (extra_major IS NULL OR length(extra_major) between 1 and 100) "
            "AND (note IS NULL OR length(note) <= 1000)",
            name="ck_education_text",
        ),
        sa.CheckConstraint(
            f"(extra_major IS NULL) = (extra_major_kind IS NULL) "
            f"AND (extra_major_kind IS NULL OR extra_major_kind IN ({_in(EXTRA_MAJOR_KINDS)}))",
            name="ck_education_extra_major",
        ),
        sa.CheckConstraint(
            "(entered_on IS NULL OR extract(day FROM entered_on) = 1) "
            "AND (graduated_on IS NULL OR extract(day FROM graduated_on) = 1)",
            name="ck_education_months",
        ),
        sa.CheckConstraint(
            "graduated_on IS NULL OR entered_on IS NULL OR graduated_on >= entered_on",
            name="ck_education_period",
        ),
        sa.CheckConstraint(
            "(gpa IS NULL) = (gpa_scale IS NULL) "
            "AND (gpa_scale IS NULL OR gpa_scale IN (4.5, 4.3, 4.0, 100)) "
            "AND (gpa IS NULL OR (gpa >= 0 AND gpa <= gpa_scale))",
            name="ck_education_gpa",
        ),
        schema=S,
    )
    op.execute(_set_null_fk("fk_education_source", "source_id", "sources"))
    op.execute(_set_null_fk("fk_education_experience", "experience_id", "experiences"))
    op.create_index(
        "ix_education_source",
        "education",
        ["workspace_id", "source_id"],
        schema=S,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    for stmt in rls.workspace_isolation_sql(S, "education"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "education", "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("education", schema=S)
