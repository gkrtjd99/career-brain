"""O02 완료 기준: 다른 공간 참조 차단 (DB 계층)."""

from uuid import uuid4

import pytest
from career_core.db import Actor, Database, Membership, Workspace
from career_core.db.scoping import scoped_fk, scoped_unique, workspace_id_column
from career_core.domain.workspace import Role
from career_core.testing import TEST_APP_LOGIN, workspace_isolation_problems
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import Column, MetaData, Table, Uuid, create_engine, func, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


async def test_owner_sees_own_workspace(db):
    ws, actor = await create_workspace(db, name="A", owner_display_name="alice")
    async with db.workspace_session(ws) as s:
        assert (await s.get(Workspace, ws)).name == "A"
        m = (await s.execute(select(Membership))).scalars().all()
    assert [(x.actor_id, x.role) for x in m] == [(actor, Role.OWNER)]


async def test_other_workspace_rows_are_invisible(db):
    ws_a, actor_a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, actor_b = await create_workspace(db, name="B", owner_display_name="bob")
    async with db.workspace_session(ws_a) as s:
        assert await s.get(Workspace, ws_b) is None
        assert await s.get(Actor, actor_b) is None
        assert await s.get(Membership, (ws_b, actor_b)) is None
        assert set((await s.execute(select(Actor.id))).scalars()) == {actor_a}
        assert await s.scalar(select(func.count()).select_from(Workspace)) == 1


async def test_cannot_write_into_other_workspace(db):
    ws_a, actor_a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, _ = await create_workspace(db, name="B", owner_display_name="bob")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.workspace_session(ws_a) as s:
            s.add(Membership(workspace_id=ws_b, actor_id=actor_a, role=Role.OWNER))


async def test_cannot_move_row_to_other_workspace(db):
    ws_a, _ = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, _ = await create_workspace(db, name="B", owner_display_name="bob")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.workspace_session(ws_a) as s:
            await s.execute(text("UPDATE core.memberships SET workspace_id = :b"), {"b": ws_b})


async def test_without_context_nothing_is_visible(db):
    await create_workspace(db, name="A", owner_display_name="alice")
    async with db.system_session() as s:
        for model in (Workspace, Actor, Membership):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_context_does_not_leak_to_pooled_connection(pg):
    db = Database(pg.app, pool_size=1, max_overflow=0)
    try:
        async with db.workspace_session(uuid4()) as s:
            assert await s.scalar(text("SELECT current_setting('app.workspace_id', true)"))
        async with db.system_session() as s:
            assert not await s.scalar(text("SELECT current_setting('app.workspace_id', true)"))
            assert await s.scalar(text("SELECT core.current_workspace_id()")) is None
    finally:
        await db.dispose()


def test_scoped_fk_blocks_cross_workspace_reference(pg):
    engine = create_engine(pg.owner, poolclass=NullPool)
    md = MetaData()
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA scoping_test"))
        Table("workspaces", md, schema="core", autoload_with=conn)
        parent = Table(
            "parent",
            md,
            Column("id", Uuid, primary_key=True),
            workspace_id_column(),
            scoped_unique(),
            schema="scoping_test",
        )
        child = Table(
            "child",
            md,
            Column("id", Uuid, primary_key=True),
            workspace_id_column(),
            Column("parent_id", Uuid, nullable=False),
            scoped_unique(),
            scoped_fk("parent_id", "scoping_test.parent"),
            schema="scoping_test",
        )
        md.create_all(conn, tables=[parent, child])
        ws_a, ws_b, p = uuid4(), uuid4(), uuid4()
        conn.execute(
            text("INSERT INTO core.workspaces (id, name) VALUES (:a, 'A'), (:b, 'B')"),
            {"a": ws_a, "b": ws_b},
        )
        conn.execute(insert(parent).values(id=p, workspace_id=ws_a))
        conn.execute(insert(child).values(id=uuid4(), workspace_id=ws_a, parent_id=p))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(child).values(id=uuid4(), workspace_id=ws_b, parent_id=p))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA scoping_test CASCADE"))
        conn.execute(
            text("DELETE FROM core.workspaces WHERE id IN (:a, :b)"), {"a": ws_a, "b": ws_b}
        )
    engine.dispose()


def test_every_workspace_table_has_forced_rls(pg):
    engine = create_engine(pg.owner, poolclass=NullPool)
    with engine.connect() as conn:
        assert workspace_isolation_problems(conn, ["core"], allow_unforced={("core", "jobs")}) == []
        forced = conn.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'core.actors'::regclass")
        ).scalar()
        assert forced
    engine.dispose()


def test_app_role_cannot_bypass_rls(pg):
    engine = create_engine(pg.owner, poolclass=NullPool)
    with engine.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": TEST_APP_LOGIN},
        ).one()
        owned = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'core' AND tableowner = :r"),
            {"r": TEST_APP_LOGIN},
        ).scalar()
    engine.dispose()
    assert (role.rolsuper, role.rolbypassrls, owned) == (False, False, 0)
