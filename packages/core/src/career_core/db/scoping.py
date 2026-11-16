"""공간 소유 테이블 규칙.

모든 사용자 데이터 테이블은
1. ``workspace_id`` 컬럼 (core.workspaces FK)
2. ``UNIQUE (workspace_id, id)``
3. 다른 공간 소유 테이블을 참조할 때 ``(workspace_id, parent_id) → parent(workspace_id, id)`` 복합 FK
4. RLS (``career_core.db.rls.workspace_isolation_sql``)
를 갖는다. UUID만 맞으면 다른 공간의 자료를 연결할 수 있는 구조를 막는다.

복합 FK에 ``ON DELETE SET NULL``을 쓰지 않는다 (workspace_id까지 NULL이 된다).
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, ForeignKeyConstraint, UniqueConstraint, Uuid

from career_core.db.base import CORE_SCHEMA


def workspace_id_column() -> Column:
    return Column(
        "workspace_id",
        Uuid,
        ForeignKey(f"{CORE_SCHEMA}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def scoped_unique() -> UniqueConstraint:
    return UniqueConstraint("workspace_id", "id")


def scoped_fk(column: str, parent: str, *, ondelete: str | None = None) -> ForeignKeyConstraint:
    """``parent``는 ``schema.table`` 형식."""
    if ondelete and ondelete.upper() == "SET NULL":
        raise ValueError("SET NULL would also null workspace_id; use CASCADE or RESTRICT")
    return ForeignKeyConstraint(
        ["workspace_id", column], [f"{parent}.workspace_id", f"{parent}.id"], ondelete=ondelete
    )
