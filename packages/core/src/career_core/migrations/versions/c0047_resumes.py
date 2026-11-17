"""core: 이력서 판본 — 브레인에서 나오는 이력서 (plan §19.5, O17)

Revision ID: c0047
Revises: c0046

- ``core.resume_versions``: 판본 하나. 종류 ``master``(마스터) · ``role``(직무별) · ``posting``(공고별). 계보는
  ``parent_id``(위 판본)와 ``source_revision_id``(복사해 온 그 판본의 revision)로 남긴다. 공고별 판본은 지원
  (``application_id``)에 이을 수 있다. 템플릿(``one_column``·``two_column``·``korean_table``), 블라인드(``blind``),
  쪽 수 한도(``max_pages``)는 판본 설정이다 (본문이 아니라서 revision 없이 바뀐다).
- ``core.resume_revisions``: 판본 본문. 저장할 때마다 새 행(앱 role은 추가·조회만, 문서 revision과 같은 규칙).
  ``content``는 구역 목록 JSON(``domain.resume``): 구역 순서·표시 여부, 항목(브레인 기록 참조 + 채울 때의 값
  복사본 + 그 값의 해시), 줄(확인한 사실 ID + 그때 문장의 해시, 또는 직접 쓴 글).
- ``core.resume_line_claims``: revision의 줄 ↔ 확인한 사실. 추가·조회만 (``document_claims``와 같다). 사실은
  지우지 않고 은퇴시키므로 ``claim_id``는 NO ACTION.
- 위 판본을 지우면 ``parent_id``는 비지만(SET NULL), usecase는 아래 판본이 있는 판본을 지우지 않는다(계보 유지).
- 판본 → 복사한 revision·위 판본 FK는 ``DEFERRABLE``(판본 ↔ revision 표 순환·같은 표 참조, 묶음 복원용).
- 세 표 모두 FORCE RLS. 다른 공간 행은 복합 FK(workspace_id, id)가 막는다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0047"
down_revision = "c0046"
branch_labels = None
depends_on = None

S = "core"
KINDS = ("master", "role", "posting")
TEMPLATES = ("one_column", "two_column", "korean_table")
ORIGINS = ("user", "agent", "copy", "ai")


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
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
        "resume_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("target_role", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("source_revision_id", sa.Uuid(), nullable=True),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("template", sa.Text(), nullable=False, server_default="one_column"),
        sa.Column("blind", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_pages", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint(_in("kind", KINDS), name="ck_resume_versions_kind"),
        sa.CheckConstraint(_in("template", TEMPLATES), name="ck_resume_versions_template"),
        sa.CheckConstraint("length(title) between 1 and 200", name="ck_resume_versions_title"),
        sa.CheckConstraint(
            "target_role is null or length(target_role) between 1 and 200",
            name="ck_resume_versions_target_role",
        ),
        sa.CheckConstraint("max_pages between 1 and 10", name="ck_resume_versions_max_pages"),
        sa.CheckConstraint("current_revision >= 0", name="ck_resume_versions_revision"),
        # 마스터는 위 판본이 없다. 지원에 잇는 것은 공고별 판본만
        sa.CheckConstraint(
            "kind <> 'master' or parent_id is null", name="ck_resume_versions_master_root"
        ),
        sa.CheckConstraint(
            "application_id is null or kind = 'posting'", name="ck_resume_versions_application"
        ),
        schema=S,
    )
    op.execute(
        _fk(
            "resume_versions",
            "fk_resume_versions_parent",
            "parent_id",
            "resume_versions",
            "SET NULL (parent_id)",
        )
    )
    op.execute(
        _fk(
            "resume_versions",
            "fk_resume_versions_application",
            "application_id",
            "applications",
            "SET NULL (application_id)",
        )
    )
    op.create_index(
        "ix_resume_versions_parent", "resume_versions", ["workspace_id", "parent_id"], schema=S
    )
    op.create_index(
        "ix_resume_versions_application",
        "resume_versions",
        ["workspace_id", "application_id"],
        schema=S,
    )

    op.create_table(
        "resume_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("version_id", "revision_no", name="uq_resume_revisions_no"),
        sa.UniqueConstraint("version_id", "idempotency_key", name="uq_resume_revisions_key"),
        sa.CheckConstraint("revision_no >= 1", name="ck_resume_revisions_no"),
        sa.CheckConstraint(_in("origin", ORIGINS), name="ck_resume_revisions_origin"),
        sa.CheckConstraint("jsonb_typeof(content) = 'object'", name="ck_resume_revisions_content"),
        schema=S,
    )
    op.execute(
        _fk(
            "resume_revisions",
            "fk_resume_revisions_version",
            "version_id",
            "resume_versions",
            "CASCADE",
        )
    )
    op.execute(
        _fk(
            "resume_versions",
            "fk_resume_versions_source_revision",
            "source_revision_id",
            "resume_revisions",
            "SET NULL (source_revision_id)",
        )
    )
    # 판본 ↔ revision 표 순환과 위 판본(같은 표): 두 FK를 미룰 수 있게 둔다 (c0011·c0030과 같은 규칙,
    # 묶음 복원이 트랜잭션 끝에서 검사)
    for name in ("fk_resume_versions_source_revision", "fk_resume_versions_parent"):
        op.execute(
            f'ALTER TABLE "{S}".resume_versions ALTER CONSTRAINT {name} '
            "DEFERRABLE INITIALLY IMMEDIATE"
        )

    op.create_table(
        "resume_line_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Text(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("revision_id", "line_id", name="uq_resume_line_claims"),
        schema=S,
    )
    op.execute(
        _fk(
            "resume_line_claims",
            "fk_resume_line_claims_revision",
            "revision_id",
            "resume_revisions",
            "CASCADE",
        )
    )
    op.execute(
        _fk("resume_line_claims", "fk_resume_line_claims_claim", "claim_id", "claims", "NO ACTION")
    )
    op.create_index(
        "ix_resume_line_claims_claim", "resume_line_claims", ["workspace_id", "claim_id"], schema=S
    )

    for table in ("resume_versions", "resume_revisions", "resume_line_claims"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    op.execute(rls.grant_app_sql(S, "resume_versions", "SELECT, INSERT, UPDATE, DELETE"))
    # 본문과 근거 연결은 추가·조회만 (판본을 지우면 FK CASCADE로 함께 지워진다)
    for table in ("resume_revisions", "resume_line_claims"):
        op.execute(rls.grant_app_sql(S, table, "SELECT, INSERT"))


def downgrade() -> None:
    op.execute(
        f'ALTER TABLE "{S}".resume_versions DROP CONSTRAINT fk_resume_versions_source_revision'
    )
    op.drop_table("resume_line_claims", schema=S)
    op.drop_table("resume_revisions", schema=S)
    op.drop_table("resume_versions", schema=S)
