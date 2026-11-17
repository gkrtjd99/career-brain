"""core: 자격·이수 기록 (O20, plan §20)

Revision ID: c0034
Revises: c0033

자격증·어학 성적·수료·수상·학력(과 재직·경력 증명 같은 그 밖의 증명)을 "사실 + 사본 파일"로 둔다. 경험(주장)이
아니다. 이력서(O17)의 자격·수료·수상·학력 구역이 여기서 온다.

- ``core.credentials``: 종류·이름·발급 기관·번호(화면은 끝 4자리만)·취득일·만료일·기간(시작·끝)·점수/등급·
  시간(수료)·상세·확인 상태(``extracted`` 파일에서 뽑음 / ``self_confirmed`` / ``evidence_supported`` 증빙 연결).
  날짜는 모두 날까지 적힌 것만 (모르면 비운다, 추측하지 않는다).
- 증빙에서 뽑은 값은 원문 위치가 있다: ``parse_run_id`` + ``field_spans`` (칸 → 블록 순번·시작·끝·쪽).
  원문 글을 이 표에 복사하지 않는다 (증빙 글은 증빙 원본의 블록에만 있다). 사람이 고친 칸은 위치를 지운다.
- ``source_id``(증빙 원본)·``experience_id``·``parse_run_id``는 같은 공간의 행만 (복합 FK). 원본·경험·파싱을
  지우면 연결만 비운다 (``ON DELETE SET NULL (칸)``, c0031과 같은 방식).
- FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0034"
down_revision = "c0033"
branch_labels = None
depends_on = None

S = "core"
KINDS = ("license", "language", "course", "award", "degree", "other")
STATUSES = ("extracted", "self_confirmed", "evidence_supported")


def _set_null_fk(name: str, column: str, table: str) -> str:
    return (
        f'ALTER TABLE "{S}".credentials ADD CONSTRAINT {name} '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}".{table} (workspace_id, id) '
        f"ON DELETE SET NULL ({column})"
    )


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    statuses = ", ".join(f"'{k}'" for k in STATUSES)
    op.create_table(
        "credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column("number", sa.Text(), nullable=True),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("score", sa.Text(), nullable=True),
        sa.Column("hours", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("experience_id", sa.Uuid(), nullable=True),
        sa.Column("parse_run_id", sa.Uuid(), nullable=True),
        sa.Column("field_spans", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_credentials_kind"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_credentials_status"),
        sa.CheckConstraint("length(name) between 1 and 200", name="ck_credentials_name"),
        sa.CheckConstraint(
            "issuer IS NULL OR length(issuer) between 1 and 200", name="ck_credentials_issuer"
        ),
        sa.CheckConstraint(
            "number IS NULL OR length(number) between 1 and 100", name="ck_credentials_number"
        ),
        sa.CheckConstraint(
            "score IS NULL OR length(score) between 1 and 100", name="ck_credentials_score"
        ),
        sa.CheckConstraint(
            "detail IS NULL OR length(detail) <= 2000", name="ck_credentials_detail"
        ),
        sa.CheckConstraint(
            "hours IS NULL OR hours between 1 and 100000", name="ck_credentials_hours"
        ),
        sa.CheckConstraint(
            "expires_on IS NULL OR acquired_on IS NULL OR expires_on >= acquired_on",
            name="ck_credentials_expiry",
        ),
        sa.CheckConstraint(
            "ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on",
            name="ck_credentials_period",
        ),
        # 증빙이 이어진 기록만 evidence_supported (증빙을 지우면 usecase가 self_confirmed로 내린다)
        sa.CheckConstraint(
            "status <> 'evidence_supported' OR source_id IS NOT NULL",
            name="ck_credentials_evidence",
        ),
        sa.CheckConstraint(
            "status = 'extracted' OR confirmed_at IS NOT NULL", name="ck_credentials_confirmed"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_spans) = 'object'", name="ck_credentials_field_spans"
        ),
        schema=S,
    )
    op.execute(_set_null_fk("fk_credentials_source", "source_id", "sources"))
    op.execute(_set_null_fk("fk_credentials_experience", "experience_id", "experiences"))
    op.execute(_set_null_fk("fk_credentials_parse_run", "parse_run_id", "parse_runs"))
    op.create_index(
        "ix_credentials_source",
        "credentials",
        ["workspace_id", "source_id"],
        schema=S,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_index(
        "ix_credentials_expires",
        "credentials",
        ["workspace_id", "expires_on"],
        schema=S,
        postgresql_where=sa.text("expires_on IS NOT NULL"),
    )
    for stmt in rls.workspace_isolation_sql(S, "credentials"):
        op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "credentials", "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("credentials", schema=S)
