"""core: 원본 용도 — 내 경험 자료·증빙·참고 (O20, plan §20)

Revision ID: c0032
Revises: c0031

- ``sources.purpose`` text NOT NULL DEFAULT ``'experience'``, CHECK (experience·proof·reference).
  기존 행은 서버 기본값으로 ``experience``가 된다 (c0032 전에는 모든 원본이 경험 추출 대상이었다).
  ``ADD COLUMN ... DEFAULT 상수``는 행을 다시 쓰지 않으므로 공간 문맥(RLS) 없이 모든 행에 적용된다.
- ``ck_sources_proof_restricted``: 증빙(``proof``)은 자료 등급 ``restricted``만. 외부 모델·외부 임베딩·
  외부 OCR로 가지 않는다는 규칙을 usecase만이 아니라 DB에서도 막는다.
- 색인 ``ix_sources_purpose`` (workspace_id, purpose): 받은 자료함의 용도 거름.
- 표 권한·RLS 정책은 표 단위라 그대로다 (앱 계정은 이미 sources를 고칠 수 있다: data_class 올리기).
"""

import sqlalchemy as sa
from alembic import op

revision = "c0032"
down_revision = "c0031"
branch_labels = None
depends_on = None

S = "core"
PURPOSES = ("experience", "proof", "reference")


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("purpose", sa.Text(), nullable=False, server_default="experience"),
        schema=S,
    )
    listed = ", ".join(f"'{p}'" for p in PURPOSES)
    op.create_check_constraint("ck_sources_purpose", "sources", f"purpose IN ({listed})", schema=S)
    op.create_check_constraint(
        "ck_sources_proof_restricted",
        "sources",
        "purpose <> 'proof' OR data_class = 'restricted'",
        schema=S,
    )
    op.create_index("ix_sources_purpose", "sources", ["workspace_id", "purpose"], schema=S)


def downgrade() -> None:
    op.drop_index("ix_sources_purpose", "sources", schema=S)
    op.drop_constraint("ck_sources_proof_restricted", "sources", schema=S)
    op.drop_constraint("ck_sources_purpose", "sources", schema=S)
    op.drop_column("sources", "purpose", schema=S)
