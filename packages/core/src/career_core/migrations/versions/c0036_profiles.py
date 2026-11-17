"""core: 프로필·증명사진 — 브레인 "나 › 프로필" (O21, plan §20)

Revision ID: c0036
Revises: c0035

- ``core.profile_photos``: 사진 변환 도구가 만든 변환본 한 장. 규격(``spec``, 사진 변환 도구의 미리 정한 크기
  id 또는 ``custom``)·가로·세로 px·바이트·형식(JPEG·PNG)·``blob_key``(BlobStore, 메타데이터를 지운 바이트의
  sha256 = ``sha256``). 원본은 두지 않는다. 같은 공간에 같은 바이트는 한 번만 (``uq_profile_photos_sha256``).
- ``core.profiles``: 공간마다 하나 (``uq_profiles_workspace``). 이름·영문 이름·생년월일(선택)·이메일·전화·
  주소·링크(``[{label, url}]``)·한 줄 소개, 기본 사진(``default_photo_id``).
  기본 사진을 사진 표의 ``is_default`` + 부분 유일 색인 대신 프로필의 칸 하나로 둔다: 기존 공간에 묶음을
  합칠 때(``merge_workspace``) 양쪽에 기본 사진이 있어도 부딪히지 않는다 (프로필은 공간당 하나라 기존 것이
  남는다). 사진을 지우면 이 칸만 비우고(SET NULL) usecase가 남은 사진 중 하나를 기본으로 잇는다.
- 모두 FORCE RLS(``workspace_isolation``), 앱 role ``SELECT, INSERT, UPDATE, DELETE``.
- 자료 등급은 ``restricted``(개인 식별 정보)로 다룬다: 모델로 보내는 경로·검색 색인이 없고 MCP에는
  이름·한 줄 소개·링크만 (``profile_summary``).
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0036"
down_revision = "c0035"
branch_labels = None
depends_on = None

S = "core"
SPECS = ("cm-3x4", "cm-3.5x4.5", "cm-2.5x3", "px-150x200", "custom")
MAX_PHOTO_BYTES = 2 * 1024 * 1024


def _workspace_fk() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    specs = ", ".join(f"'{s}'" for s in SPECS)
    op.create_table(
        "profile_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "sha256", name="uq_profile_photos_sha256"),
        sa.CheckConstraint(f"spec IN ({specs})", name="ck_profile_photos_spec"),
        sa.CheckConstraint(
            "width_px between 16 and 4000 AND height_px between 16 and 4000",
            name="ck_profile_photos_size",
        ),
        sa.CheckConstraint(
            f"bytes between 1 and {MAX_PHOTO_BYTES}", name="ck_profile_photos_bytes"
        ),
        sa.CheckConstraint("mime IN ('image/jpeg', 'image/png')", name="ck_profile_photos_mime"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND blob_key = sha256", name="ck_profile_photos_blob"
        ),
        schema=S,
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("name_en", sa.Text(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("links", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("default_photo_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", name="uq_profiles_workspace"),
        sa.CheckConstraint(
            "(name IS NULL OR length(name) <= 100) AND (name_en IS NULL OR length(name_en) <= 100)",
            name="ck_profiles_name",
        ),
        sa.CheckConstraint(
            "(email IS NULL OR length(email) <= 254) AND (phone IS NULL OR length(phone) <= 40) "
            "AND (address IS NULL OR length(address) <= 200)",
            name="ck_profiles_contact",
        ),
        sa.CheckConstraint(
            "headline IS NULL OR length(headline) <= 200", name="ck_profiles_headline"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= 10",
            name="ck_profiles_links",
        ),
        sa.CheckConstraint(
            "birth_date IS NULL OR birth_date >= DATE '1900-01-01'", name="ck_profiles_birth_date"
        ),
        schema=S,
    )
    # 사진을 지우면 기본 사진 칸만 비운다 (workspace_id는 그대로: 칸을 지정한 SET NULL, c0031과 같은 방식)
    op.execute(
        f'ALTER TABLE "{S}".profiles ADD CONSTRAINT fk_profiles_default_photo '
        f'FOREIGN KEY (workspace_id, default_photo_id) REFERENCES "{S}".profile_photos '
        "(workspace_id, id) ON DELETE SET NULL (default_photo_id)"
    )
    for table in ("profile_photos", "profiles"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))


def downgrade() -> None:
    op.drop_table("profiles", schema=S)
    op.drop_table("profile_photos", schema=S)
