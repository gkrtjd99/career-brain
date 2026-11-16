"""core: 판정 모델 결과 캐시 (같은 질문에 두 번 돈을 내지 않는다)

Revision ID: c0022
Revises: c0021

- ``core.judge_cache``: (용도, 라우트 모델, 프롬프트 버전, 입력 해시)마다 모델 응답 한 벌.
  입력 해시는 시스템 프롬프트·메시지·최대 출력·자료 등급의 sha256이라 요건·글이 한 글자라도 바뀌면 다른 행이다.
- 응답에는 문서 인용·이유(사용자 글의 조각)가 들어 있어 공간 RLS(FORCE)를 건다.
- 만료(``expires_at``, 기본 30일)가 지나면 쓰지 않고, 같은 공간에 새로 쓸 때 지운다.
- 내보내기 묶음에는 넣지 않는다 (export.EXCLUDED_TABLES): 다시 만들 수 있는 파생 자료이고, 지운 문서의 인용을
  백업으로 옮기지 않기 위해서다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0022"
down_revision = "c0021"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.create_table(
        "judge_cache",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),  # 라우트 "<endpoint>/<model>"
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("result_model", sa.Text(), nullable=False),  # 제공자가 돌려준 모델 이름
        sa.Column(
            "cost_usd", sa.Numeric(18, 10), nullable=True
        ),  # 처음 부를 때 든 비용 (아낀 값 표시용)
        sa.Column("cost_basis", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),  # 처음 부른 장부 행 (core.model_calls)
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id",
            "purpose",
            "model",
            "prompt_version",
            "input_hash",
            name="uq_judge_cache_key",
        ),
        sa.CheckConstraint(
            "purpose IN ('judge_claim', 'judge_visibility')", name="ck_judge_cache_purpose"
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_judge_cache_input_hash"),
        sa.CheckConstraint("length(response_text) <= 65536", name="ck_judge_cache_response_size"),
        sa.CheckConstraint("hits >= 0", name="ck_judge_cache_hits"),
        schema=S,
    )
    op.create_index(
        "ix_judge_cache_expires", "judge_cache", ["workspace_id", "expires_at"], schema=S
    )
    for stmt in rls.workspace_isolation_sql(S, "judge_cache"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "judge_cache", "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("judge_cache", schema=S)
