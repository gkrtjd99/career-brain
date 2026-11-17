"""core: 경험 구조 — 종류·상하(한 단계)·월 단위 기간·태그 (O19, plan §20)

Revision ID: c0031
Revises: c0030

- ``experiences.kind`` text NOT NULL DEFAULT ``'other'`` (work·project·education·activity·other).
- ``experiences.parent_id`` uuid NULL: 같은 공간의 경험 하나 (FK ``(workspace_id, parent_id)``).
  부모를 지우면 자식은 맨 위로 (``ON DELETE SET NULL (parent_id)``). 같은 표를 가리키므로 c0012처럼
  ``DEFERRABLE INITIALLY IMMEDIATE``: 복원 때만 트랜잭션 끝에서 검사한다. 한 단계만: trigger
  ``experience_one_level``이 자식의 자식·자식이 된 부모·자기 자신을 막는다 (usecase도 먼저 검사한다).
- ``experiences.started_on``·``ended_on`` date NULL: 월 단위, 그 달 1일. ``ongoing`` bool(진행 중이면 끝 없음).
- ``experiences.tags`` text[] NOT NULL DEFAULT ``'{}'`` (30개·40자 이하, 정규화는 usecase).
- 채우기: 기존 행의 ``period_text``에서 시작·끝 달이 분명할 때만 날짜를 채운다
  (``domain.experiences.parse_period``). 모호하면 비우고, ``period_text``는 그대로 둔다.
  공간 문맥 없이 모든 행을 고치려고 채우는 동안만 NO FORCE (c0021과 같은 방식).
"""

import sqlalchemy as sa
from alembic import op
from career_core.domain.experiences import parse_period

revision = "c0031"
down_revision = "c0030"
branch_labels = None
depends_on = None

S = "core"
KINDS = ("work", "project", "education", "activity", "other")

_FUNCTION = """
CREATE FUNCTION "{s}".experience_one_level() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.parent_id IS NULL THEN
    RETURN NEW;
  END IF;
  IF NEW.parent_id = NEW.id THEN
    RAISE EXCEPTION 'experience_parent_self' USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM "{s}".experiences p
             WHERE p.workspace_id = NEW.workspace_id AND p.id = NEW.parent_id
               AND p.parent_id IS NOT NULL) THEN
    RAISE EXCEPTION 'experience_parent_is_child' USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM "{s}".experiences c
             WHERE c.workspace_id = NEW.workspace_id AND c.parent_id = NEW.id) THEN
    RAISE EXCEPTION 'experience_has_children' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.add_column(
        "experiences",
        sa.Column("kind", sa.Text(), nullable=False, server_default="other"),
        schema=S,
    )
    op.add_column("experiences", sa.Column("parent_id", sa.Uuid()), schema=S)
    op.add_column("experiences", sa.Column("started_on", sa.Date()), schema=S)
    op.add_column("experiences", sa.Column("ended_on", sa.Date()), schema=S)
    op.add_column(
        "experiences",
        sa.Column("ongoing", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=S,
    )
    op.add_column(
        "experiences",
        sa.Column(
            "tags", sa.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        schema=S,
    )
    listed = ", ".join(f"'{k}'" for k in KINDS)
    op.create_check_constraint(
        "ck_experiences_kind", "experiences", f"kind IN ({listed})", schema=S
    )
    op.create_check_constraint(
        "ck_experiences_months",
        "experiences",
        "(started_on IS NULL OR extract(day FROM started_on) = 1) "
        "AND (ended_on IS NULL OR extract(day FROM ended_on) = 1)",
        schema=S,
    )
    op.create_check_constraint(
        "ck_experiences_period",
        "experiences",
        "(ended_on IS NULL OR (started_on IS NOT NULL AND ended_on >= started_on)) "
        "AND NOT (ongoing AND ended_on IS NOT NULL)",
        schema=S,
    )
    op.create_check_constraint(
        "ck_experiences_tags",
        "experiences",
        "cardinality(tags) <= 30 AND array_position(tags, NULL) IS NULL",
        schema=S,
    )
    op.create_check_constraint(
        "ck_experiences_parent_self",
        "experiences",
        "parent_id IS NULL OR parent_id <> id",
        schema=S,
    )
    op.execute(
        f'ALTER TABLE "{S}".experiences ADD CONSTRAINT fk_experiences_parent '
        f'FOREIGN KEY (workspace_id, parent_id) REFERENCES "{S}".experiences (workspace_id, id) '
        "ON DELETE SET NULL (parent_id) DEFERRABLE INITIALLY IMMEDIATE"
    )
    op.create_index(
        "ix_experiences_parent",
        "experiences",
        ["workspace_id", "parent_id"],
        schema=S,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.execute(_FUNCTION.format(s=S))
    op.execute(
        f"CREATE TRIGGER experience_one_level BEFORE INSERT OR UPDATE OF parent_id "
        f'ON "{S}".experiences FOR EACH ROW EXECUTE FUNCTION "{S}".experience_one_level()'
    )

    # 기존 행 채우기: 원문 기간이 분명한 행만. NO FORCE 동안만 소유자가 모든 공간의 행을 본다.
    op.execute(f'ALTER TABLE "{S}".experiences NO FORCE ROW LEVEL SECURITY')
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f'SELECT id, period_text FROM "{S}".experiences WHERE period_text IS NOT NULL')
    ).all()
    for row in rows:
        period = parse_period(row.period_text)
        if period is None:
            continue
        conn.execute(
            sa.text(
                f'UPDATE "{S}".experiences SET started_on = :s, ended_on = :e, ongoing = :o '
                "WHERE id = :i"
            ),
            {"s": period.started_on, "e": period.ended_on, "o": period.ongoing, "i": row.id},
        )
    op.execute(f'ALTER TABLE "{S}".experiences FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute(f'DROP TRIGGER experience_one_level ON "{S}".experiences')
    op.execute(f'DROP FUNCTION "{S}".experience_one_level()')
    op.drop_index("ix_experiences_parent", "experiences", schema=S)
    op.drop_constraint("fk_experiences_parent", "experiences", schema=S)
    for name in (
        "ck_experiences_parent_self",
        "ck_experiences_tags",
        "ck_experiences_period",
        "ck_experiences_months",
        "ck_experiences_kind",
    ):
        op.drop_constraint(name, "experiences", schema=S)
    for column in ("tags", "ongoing", "ended_on", "started_on", "parent_id", "kind"):
        op.drop_column("experiences", column, schema=S)
