"""core: 원본 파싱에서 숨김 서식 글을 뺄지 (parse_runs.hidden_text)

Revision ID: c0029
Revises: c0025

- ``core.parse_runs.hidden_text``: ``keep``(기본, 지금까지와 같음 — 숨김 서식 글도 블록에 넣고 검사가 경고)
  또는 ``exclude``(DOCX ``w:vanish``·``specVanish``·``webHidden`` run을, 스타일 상속까지 숨김 글 검사와 같은
  규칙으로 블록에서 뺀다). 업로드 때 정하고, 그 파싱 실행의 블록이 어떻게 만들어졌는지 기록한다.
- 지금은 DOCX만 따른다. 다른 형식으로 ``exclude``를 요청하면 ``keep``으로 저장한다 (업로드 응답에 실제 값).
- 기존 행은 ``keep``. 표 권한·RLS는 그대로 (칸 하나 더하기).
"""

import sqlalchemy as sa
from alembic import op

revision = "c0029"
down_revision = "c0025"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.add_column(
        "parse_runs",
        sa.Column("hidden_text", sa.Text(), nullable=False, server_default="keep"),
        schema=S,
    )
    op.create_check_constraint(
        "ck_parse_runs_hidden_text",
        "parse_runs",
        "hidden_text IN ('keep', 'exclude')",
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint("ck_parse_runs_hidden_text", "parse_runs", schema=S)
    op.drop_column("parse_runs", "hidden_text", schema=S)
