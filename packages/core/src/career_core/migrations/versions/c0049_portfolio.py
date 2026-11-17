"""core: 포트폴리오 — 경험 하나에 붙는 항목, 이미지, 골라 순서를 정한 묶음 (plan §19.6, O18)

Revision ID: c0049
Revises: c0048

- ``core.portfolio_items``: 항목 하나. **경험 하나에 붙는다** (``experience_id`` NOT NULL, 경험은 지우지 않고 보관하므로
  NO ACTION). 제목·공개 범위(``visibility``)·기술 태그·링크(저장소·데모·글·영상)는 설정이라 revision 없이 바뀐다.
  공개 범위: ``public``(공개 가능) · ``application``(지원서에만) · ``private``(나만 — 어떤 출력에도 안 나간다).
- ``core.portfolio_item_revisions``: 항목 본문("문제 → 내 역할 → 한 일 → 결과", ``domain.portfolio``). 저장할 때마다
  새 행(앱 role은 추가·조회만, 이력서 revision과 같은 규칙).
- ``core.portfolio_line_claims``: revision의 줄 ↔ 확인한 사실 (``resume_line_claims``와 같다).
- ``core.portfolio_images``: 스크린샷·구성도. 브라우저에서 긴 변 1600px·1MiB 안으로 줄여 다시 인코딩한 파일을 서버가
  다시 검사하고 메타데이터를 한 번 더 지운 바이트(BlobStore ``blob_key``). 이미지마다 공개 범위(기본 ``private``).
  DB도 크기·용량 한도를 확인한다. 모델·MCP로 바이트를 보내는 경로가 없다.
- ``core.portfolio_bundles``: 묶음(전체 ``all`` · 직무별 ``role`` · 공고별 ``posting``). 계보는 이력서 판본처럼
  ``parent_id``(위 묶음)와 ``source_revision``(복사할 때의 위 묶음 revision). 공고별은 지원에 이을 수 있다.
  ``revision``은 항목 목록을 바꿀 때마다 오른다(409용).
- ``core.portfolio_bundle_items``: 묶음에 넣은 항목과 순서. 항목을 지우면 같이 빠진다.
- ``core.github_tokens``: 비공개 저장소를 읽는 사용자 토큰(선택). 모델 연결 키와 같은 ``SecretBox``(Fernet) 암호문과
  끝 4자리만. 공간마다 하나. **내보내기에서 뺀다** (``export.EXCLUDED_TABLES``).
- 모든 표 FORCE RLS. 다른 공간 행은 복합 FK(workspace_id, id)가 막는다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "c0049"
down_revision = "c0048"
branch_labels = None
depends_on = None

S = "core"
VISIBILITIES = ("public", "application", "private")
ORIGINS = ("user", "agent")
IMAGE_KINDS = ("screenshot", "diagram")
IMAGE_MIMES = ("image/jpeg", "image/png")
BUNDLE_KINDS = ("all", "role", "posting")
MAX_EDGE = 1600
MAX_IMAGE_BYTES = 1024 * 1024


def _ws(unique: bool = False) -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=unique,
    )


def _fk(table: str, name: str, column: str, parent: str, action: str) -> str:
    return (
        f'ALTER TABLE "{S}".{table} ADD CONSTRAINT {name} '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}".{parent} (workspace_id, id) '
        f"ON DELETE {action}"
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "portfolio_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("experience_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="public"),
        sa.Column("tags", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("links", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(_in("visibility", VISIBILITIES), name="ck_portfolio_items_visibility"),
        sa.CheckConstraint("length(title) between 1 and 200", name="ck_portfolio_items_title"),
        sa.CheckConstraint("cardinality(tags) <= 20", name="ck_portfolio_items_tags"),
        sa.CheckConstraint(
            "jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= 10 "
            "AND pg_column_size(links) <= 50000",
            name="ck_portfolio_items_links",
        ),
        sa.CheckConstraint("current_revision >= 0", name="ck_portfolio_items_revision"),
        schema=S,
    )
    op.execute(
        _fk(
            "portfolio_items",
            "fk_portfolio_items_experience",
            "experience_id",
            "experiences",
            "NO ACTION",
        )
    )
    op.create_index(
        "ix_portfolio_items_experience",
        "portfolio_items",
        ["workspace_id", "experience_id"],
        schema=S,
    )

    op.create_table(
        "portfolio_item_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("item_id", "revision_no", name="uq_portfolio_item_revisions_no"),
        sa.UniqueConstraint("item_id", "idempotency_key", name="uq_portfolio_item_revisions_key"),
        sa.CheckConstraint("revision_no >= 1", name="ck_portfolio_item_revisions_no"),
        sa.CheckConstraint(_in("origin", ORIGINS), name="ck_portfolio_item_revisions_origin"),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object' AND pg_column_size(content) <= 200000",
            name="ck_portfolio_item_revisions_content",
        ),
        schema=S,
    )
    op.execute(
        _fk(
            "portfolio_item_revisions",
            "fk_portfolio_item_revisions_item",
            "item_id",
            "portfolio_items",
            "CASCADE",
        )
    )

    op.create_table(
        "portfolio_line_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Text(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("revision_id", "line_id", name="uq_portfolio_line_claims"),
        schema=S,
    )
    op.execute(
        _fk(
            "portfolio_line_claims",
            "fk_portfolio_line_claims_revision",
            "revision_id",
            "portfolio_item_revisions",
            "CASCADE",
        )
    )
    op.execute(
        _fk(
            "portfolio_line_claims",
            "fk_portfolio_line_claims_claim",
            "claim_id",
            "claims",
            "NO ACTION",
        )
    )
    op.create_index(
        "ix_portfolio_line_claims_claim",
        "portfolio_line_claims",
        ["workspace_id", "claim_id"],
        schema=S,
    )

    op.create_table(
        "portfolio_images",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="screenshot"),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="private"),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("item_id", "sha256", name="uq_portfolio_images_sha"),
        sa.CheckConstraint(_in("kind", IMAGE_KINDS), name="ck_portfolio_images_kind"),
        sa.CheckConstraint(_in("visibility", VISIBILITIES), name="ck_portfolio_images_visibility"),
        sa.CheckConstraint(_in("mime", IMAGE_MIMES), name="ck_portfolio_images_mime"),
        sa.CheckConstraint(
            f"width_px between 16 and {MAX_EDGE} AND height_px between 16 and {MAX_EDGE}",
            name="ck_portfolio_images_size",
        ),
        sa.CheckConstraint(
            f"bytes between 1 and {MAX_IMAGE_BYTES}", name="ck_portfolio_images_bytes"
        ),
        sa.CheckConstraint(
            "caption is null or length(caption) between 1 and 200",
            name="ck_portfolio_images_caption",
        ),
        sa.CheckConstraint("position >= 0", name="ck_portfolio_images_position"),
        schema=S,
    )
    op.execute(
        _fk("portfolio_images", "fk_portfolio_images_item", "item_id", "portfolio_items", "CASCADE")
    )

    op.create_table(
        "portfolio_bundles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("target_role", sa.Text(), nullable=True),
        sa.Column("intro", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("source_revision", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(_in("kind", BUNDLE_KINDS), name="ck_portfolio_bundles_kind"),
        sa.CheckConstraint("length(title) between 1 and 200", name="ck_portfolio_bundles_title"),
        sa.CheckConstraint(
            "target_role is null or length(target_role) between 1 and 200",
            name="ck_portfolio_bundles_target_role",
        ),
        sa.CheckConstraint(
            "intro is null or length(intro) between 1 and 1000", name="ck_portfolio_bundles_intro"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_portfolio_bundles_revision"),
        sa.CheckConstraint(
            "kind <> 'all' or parent_id is null", name="ck_portfolio_bundles_all_root"
        ),
        sa.CheckConstraint(
            "application_id is null or kind = 'posting'", name="ck_portfolio_bundles_application"
        ),
        schema=S,
    )
    op.execute(
        _fk(
            "portfolio_bundles",
            "fk_portfolio_bundles_parent",
            "parent_id",
            "portfolio_bundles",
            "SET NULL (parent_id)",
        )
    )
    # 위 묶음은 같은 표: 묶음 복원이 트랜잭션 끝에서 검사하도록 미룰 수 있게 둔다 (c0047과 같다)
    op.execute(
        f'ALTER TABLE "{S}".portfolio_bundles ALTER CONSTRAINT fk_portfolio_bundles_parent '
        "DEFERRABLE INITIALLY IMMEDIATE"
    )
    op.execute(
        _fk(
            "portfolio_bundles",
            "fk_portfolio_bundles_application",
            "application_id",
            "applications",
            "SET NULL (application_id)",
        )
    )
    op.create_index(
        "ix_portfolio_bundles_parent", "portfolio_bundles", ["workspace_id", "parent_id"], schema=S
    )
    op.create_index(
        "ix_portfolio_bundles_application",
        "portfolio_bundles",
        ["workspace_id", "application_id"],
        schema=S,
    )

    op.create_table(
        "portfolio_bundle_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("bundle_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("bundle_id", "item_id", name="uq_portfolio_bundle_items"),
        sa.CheckConstraint("position >= 0", name="ck_portfolio_bundle_items_position"),
        schema=S,
    )
    op.execute(
        _fk(
            "portfolio_bundle_items",
            "fk_portfolio_bundle_items_bundle",
            "bundle_id",
            "portfolio_bundles",
            "CASCADE",
        )
    )
    op.execute(
        _fk(
            "portfolio_bundle_items",
            "fk_portfolio_bundle_items_item",
            "item_id",
            "portfolio_items",
            "CASCADE",
        )
    )
    op.create_index(
        "ix_portfolio_bundle_items_item",
        "portfolio_bundle_items",
        ["workspace_id", "item_id"],
        schema=S,
    )

    op.create_table(
        "github_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(unique=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("last4", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("length(ciphertext) <= 4096", name="ck_github_tokens_size"),
        sa.CheckConstraint("length(last4) <= 4", name="ck_github_tokens_last4"),
        schema=S,
    )

    tables = (
        "portfolio_items",
        "portfolio_item_revisions",
        "portfolio_line_claims",
        "portfolio_images",
        "portfolio_bundles",
        "portfolio_bundle_items",
        "github_tokens",
    )
    for table in tables:
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    for table in (
        "portfolio_items",
        "portfolio_images",
        "portfolio_bundles",
        "portfolio_bundle_items",
        "github_tokens",
    ):
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT, UPDATE, DELETE"))
    # 본문과 근거 연결은 추가·조회만 (항목을 지우면 FK CASCADE로 함께 지워진다)
    for table in ("portfolio_item_revisions", "portfolio_line_claims"):
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))


def downgrade() -> None:
    for table in (
        "github_tokens",
        "portfolio_bundle_items",
        "portfolio_bundles",
        "portfolio_images",
        "portfolio_line_claims",
        "portfolio_item_revisions",
        "portfolio_items",
    ):
        op.drop_table(table, schema=S)
