"""core: study search kinds, item merge, stale requirement cleanup, study settings (timezone)

Revision ID: c0013
Revises: c0011
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0013"
down_revision = "c0011"
branch_labels = None
depends_on = None

S = "core"

OLD_TARGET_KINDS = ("claim", "block", "answer", "job")
# 학습 메모(study_sessions.notes)·오답(study_mistakes)도 검색 색인 대상이다. 둘 다 학습 기록이지 경험이 아니다.
TARGET_KINDS = (*OLD_TARGET_KINDS, "study_note", "study_mistake")
OLD_ITEM_STATUSES = ("planned", "in_progress", "done", "dropped")
ITEM_STATUSES = (*OLD_ITEM_STATUSES, "merged")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_embeddings_target_kind", "embeddings", schema=S)
    op.create_check_constraint(
        "ck_embeddings_target_kind", "embeddings", _in("target_kind", TARGET_KINDS), schema=S
    )

    # 학습 항목 병합: 합쳐진 항목은 status = merged + study_item_merges 한 행(→ 합친 항목).
    # 응시·후보(불변)는 옮기지 않고 원래 항목에 남긴 채 합친 항목에서 함께 보인다.
    # 학습 기록·오답(고칠 수 있는 행)은 옮긴다. 자기 참조 FK 대신 별도 표로 두어 내보내기 순서가 단순하다.
    op.drop_constraint("ck_study_items_status", "study_items", schema=S)
    op.create_check_constraint(
        "ck_study_items_status", "study_items", _in("status", ITEM_STATUSES), schema=S
    )
    op.create_table(
        "study_item_merges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("target_item_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("source_item_id", name="uq_study_item_merges_source"),
        sa.CheckConstraint(
            "source_item_id <> target_item_id", name="ck_study_item_merges_distinct"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_item_id"],
            [f"{S}.study_items.workspace_id", f"{S}.study_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "target_item_id"],
            [f"{S}.study_items.workspace_id", f"{S}.study_items.id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    op.create_index(
        "ix_study_item_merges_target", "study_item_merges", ["target_item_id"], schema=S
    )
    for stmt in rls.workspace_isolation_sql(S, "study_item_merges"):
        op.execute(stmt)
    # 합친 항목을 다시 다른 항목에 합치면 대상만 바꾼다 (병합은 늘 한 단계).
    op.execute(rls.grant_app_sql(S, "study_item_merges", "SELECT, INSERT, UPDATE, DELETE"))

    # 공고에서 빠진 요건(current: false) 정리. 문장은 여전히 고칠 수 없고(UPDATE 없음) 지우기만 한다.
    op.execute(rls.grant_app_sql(S, "study_requirements", "DELETE"))

    # 공간의 학습 설정. 지금은 표시 시간대 하나 (복습 마감 = 이 시간대의 하루 끝, D-day).
    op.create_table(
        "study_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", name="uq_study_settings_workspace"),
        sa.CheckConstraint("length(timezone) between 1 and 64", name="ck_study_settings_timezone"),
        schema=S,
    )
    for stmt in rls.workspace_isolation_sql(S, "study_settings"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "study_settings", "SELECT, INSERT, UPDATE"))


def downgrade() -> None:
    op.drop_table("study_settings", schema=S)
    op.execute(f'REVOKE DELETE ON "{S}"."study_requirements" FROM {rls.APP_ROLE}')
    # 합쳐진 항목은 지우지 않고(응시 기록이 따라 지워진다) 그만둔 항목으로 되돌린다.
    op.execute(f"UPDATE {S}.study_items SET status = 'dropped' WHERE status = 'merged'")
    op.drop_table("study_item_merges", schema=S)
    op.drop_constraint("ck_study_items_status", "study_items", schema=S)
    op.create_check_constraint(
        "ck_study_items_status", "study_items", _in("status", OLD_ITEM_STATUSES), schema=S
    )
    op.execute(f"DELETE FROM {S}.embeddings WHERE target_kind in ('study_note', 'study_mistake')")
    op.drop_constraint("ck_embeddings_target_kind", "embeddings", schema=S)
    op.create_check_constraint(
        "ck_embeddings_target_kind", "embeddings", _in("target_kind", OLD_TARGET_KINDS), schema=S
    )
