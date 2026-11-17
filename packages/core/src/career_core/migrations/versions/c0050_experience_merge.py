"""core: 같은 경험 합치기 · "같은 경험일 수 있음" 표시 · 추출 결과의 다른 종류 제안 (2026-11-18 사용 점검)

Revision ID: c0050
Revises: c0049

- ``core.experiences.merged_into_id``: 이 경험을 합친 대상 경험 (같은 공간, FK ``(workspace_id, merged_into_id)``,
  대상을 지우면 이 칸만 비운다, 같은 표를 가리키니 c0031 parent처럼 DEFERRABLE INITIALLY IMMEDIATE — 복원 때만 끝에서 검사). 합친 경험은 ``status = 'retired'``로 남아 옛 이력서 revision(추가·조회만)의
  참조가 대상 경험으로 이어진다. ``merged_at``: 합친 시각. 합친 경험만 이 칸이 있다 (CHECK).
- ``core.experiences.possible_duplicate_of``: 추출이 "같은 경험일 수 있음"으로 본 기존 경험 (확실하지 않아 새로
  만들었다). 사람이 합치거나 "다른 경험"으로 표시하면 비운다. 대상을 지우면 비운다.
- ``core.extraction_runs.experiences_matched``·``claims_attached``: 새로 만들지 않고 기존 경험에 붙인 경험 수와
  기존 사실에 근거로 붙인(같은 문장) 수. ``suggestions``(JSONB): 경험이 아닌 줄 — 학력 제안(학력 표에는 확인
  상태가 없어 자동으로 만들지 않는다), 기술 줄(경험 태그로 옮기지 못한 기술명), 걸러 낸 수.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0050"
down_revision = "c0049"
branch_labels = None
depends_on = None

S = "core"


def _set_null_fk(name: str, column: str) -> str:
    return (
        f'ALTER TABLE "{S}".experiences ADD CONSTRAINT {name} '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}".experiences (workspace_id, id) '
        f"ON DELETE SET NULL ({column}) DEFERRABLE INITIALLY IMMEDIATE"
    )


def upgrade() -> None:
    op.add_column("experiences", sa.Column("merged_into_id", sa.Uuid()), schema=S)
    op.add_column("experiences", sa.Column("merged_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("experiences", sa.Column("possible_duplicate_of", sa.Uuid()), schema=S)
    op.execute(_set_null_fk("fk_experiences_merged_into", "merged_into_id"))
    op.execute(_set_null_fk("fk_experiences_possible_duplicate", "possible_duplicate_of"))
    op.create_check_constraint(
        "ck_experiences_merged",
        "experiences",
        "(merged_into_id IS NULL OR (status = 'retired' AND merged_into_id <> id)) "
        "AND (merged_at IS NULL OR status = 'retired')",
        schema=S,
    )
    op.create_check_constraint(
        "ck_experiences_possible_duplicate",
        "experiences",
        "possible_duplicate_of IS NULL OR possible_duplicate_of <> id",
        schema=S,
    )
    op.create_index(
        "ix_experiences_merged_into",
        "experiences",
        ["workspace_id", "merged_into_id"],
        schema=S,
        postgresql_where=sa.text("merged_into_id IS NOT NULL"),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("experiences_matched", sa.Integer(), nullable=False, server_default="0"),
        schema=S,
    )
    op.add_column(
        "extraction_runs",
        sa.Column("claims_attached", sa.Integer(), nullable=False, server_default="0"),
        schema=S,
    )
    op.add_column(
        "extraction_runs",
        sa.Column("suggestions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        schema=S,
    )
    op.create_check_constraint(
        "ck_extraction_runs_suggestions",
        "extraction_runs",
        "jsonb_typeof(suggestions) = 'object' AND pg_column_size(suggestions) <= 200000",
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint("ck_extraction_runs_suggestions", "extraction_runs", schema=S, type_="check")
    for column in ("suggestions", "claims_attached", "experiences_matched"):
        op.drop_column("extraction_runs", column, schema=S)
    op.drop_index("ix_experiences_merged_into", "experiences", schema=S)
    op.drop_constraint("ck_experiences_possible_duplicate", "experiences", schema=S, type_="check")
    op.drop_constraint("ck_experiences_merged", "experiences", schema=S, type_="check")
    op.drop_constraint("fk_experiences_possible_duplicate", "experiences", schema=S)
    op.drop_constraint("fk_experiences_merged_into", "experiences", schema=S)
    for column in ("possible_duplicate_of", "merged_at", "merged_into_id"):
        op.drop_column("experiences", column, schema=S)
