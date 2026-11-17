"""core: 브레인에 넣기 — 용도 확인 대기, 이 기기에서 읽은 글 (plan §9, §20)

Revision ID: c0039
Revises: c0036

- ``sources.purpose_pending`` boolean NOT NULL DEFAULT false: 넣을 때 용도를 확신하지 못해 사람의 확인을
  기다린다 (받은 자료함 "확인 필요"). 기존 행은 서버 기본값으로 false (사람이 골라 올린 자료다).
  확인을 기다리는 원본은 자료 등급 ``restricted`` (``ck_sources_pending_restricted``): 증빙일 수 있는 자료가
  확인 전에 외부 모델·외부 OCR로 가지 않는다.
- ``sources.purpose_hint`` text NULL (200자 이하): 제안의 이유 (걸린 낱말, ``resident_number``, ``image``).
- ``parse_runs.text_layer`` jsonb NULL: 브라우저(이 기기)나 연결된 모델이 읽은 글의 기록
  ``{origin: device|model, engine, pages, blocks, chars, masked_resident_numbers, at}``. 글 자체는
  ``document_blocks``(``origin`` = ``device_ocr`` / ``model_ocr:<연결>``)에 있다.
- ``document_blocks.bbox`` jsonb NULL: 읽은 글의 쪽 안 위치 ``[x0, y0, x1, y1]`` (그 쪽 그림의 px).
  OCR이 준 것만 넣는다 — 없는 좌표를 만들지 않는다 (plan §9).
- 표 권한·RLS 정책은 표 단위라 그대로다.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0039"
down_revision = "c0036"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("purpose_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )
    op.add_column("sources", sa.Column("purpose_hint", sa.Text(), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_sources_purpose_hint_len",
        "sources",
        "purpose_hint IS NULL OR char_length(purpose_hint) <= 200",
        schema=S,
    )
    op.create_check_constraint(
        "ck_sources_pending_restricted",
        "sources",
        "NOT purpose_pending OR data_class = 'restricted'",
        schema=S,
    )
    op.create_index(
        "ix_sources_purpose_pending",
        "sources",
        ["workspace_id"],
        schema=S,
        postgresql_where=sa.text("purpose_pending"),
    )
    op.add_column("parse_runs", sa.Column("text_layer", JSONB(), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_parse_runs_text_layer_object",
        "parse_runs",
        "text_layer IS NULL OR jsonb_typeof(text_layer) = 'object'",
        schema=S,
    )
    op.add_column("document_blocks", sa.Column("bbox", JSONB(), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_document_blocks_bbox",
        "document_blocks",
        "bbox IS NULL OR (jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4)",
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint("ck_document_blocks_bbox", "document_blocks", schema=S)
    op.drop_column("document_blocks", "bbox", schema=S)
    op.drop_constraint("ck_parse_runs_text_layer_object", "parse_runs", schema=S)
    op.drop_column("parse_runs", "text_layer", schema=S)
    op.drop_index("ix_sources_purpose_pending", "sources", schema=S)
    op.drop_constraint("ck_sources_pending_restricted", "sources", schema=S)
    op.drop_constraint("ck_sources_purpose_hint_len", "sources", schema=S)
    op.drop_column("sources", "purpose_hint", schema=S)
    op.drop_column("sources", "purpose_pending", schema=S)
