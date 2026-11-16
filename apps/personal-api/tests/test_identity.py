import httpx
import pytest
from career_core.domain.workspace import ROLE_SCOPES, Role
from career_core.testing import workspace_isolation_problems
from career_core.usecases.workspaces import create_workspace
from personal_api.identity import ApiToken, PersonalTokenResolver, issue_token, revoke_token
from personal_api.main import create_app
from personal_api.settings import Settings
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


async def _workspace_with_token(db, name):
    ws, actor = await create_workspace(db, name=name, owner_display_name=name.lower())
    return ws, actor, await issue_token(db, workspace_id=ws, actor_id=actor, label="test")


async def test_token_resolves_to_owner_of_its_workspace(db):
    ws, actor, token = await _workspace_with_token(db, "A")
    ctx = await PersonalTokenResolver(db).resolve(token)
    assert (ctx.workspace_id, ctx.subject_id, ctx.scopes) == (ws, actor, ROLE_SCOPES[Role.OWNER])


@pytest.mark.parametrize("bad", ["", "cbp_unknown", "not-a-token"])
async def test_unknown_tokens_resolve_to_none(db, bad):
    await _workspace_with_token(db, "A")
    assert await PersonalTokenResolver(db).resolve(bad) is None


async def test_revoked_token_stops_working(db):
    ws, _, token = await _workspace_with_token(db, "A")
    assert await revoke_token(db, workspace_id=ws, token=token)
    assert await PersonalTokenResolver(db).resolve(token) is None


async def test_tokens_of_other_workspaces_are_invisible(db):
    ws_a, _, _ = await _workspace_with_token(db, "A")
    _, _, token_b = await _workspace_with_token(db, "B")
    async with db.workspace_session(ws_a) as s:
        assert set((await s.execute(select(ApiToken.workspace_id))).scalars()) == {ws_a}
    async with db.system_session() as s:
        assert await s.scalar(select(func.count()).select_from(ApiToken)) == 0
    # 다른 공간의 토큰을 자기 공간 문맥에서 폐기할 수 없다
    assert not await revoke_token(db, workspace_id=ws_a, token=token_b)


async def test_http_requires_token_and_stays_in_its_workspace(pg, db):
    ws_a, actor_a, token_a = await _workspace_with_token(db, "A")
    await _workspace_with_token(db, "B")
    app = create_app(Settings(database_url=pg.app.render_as_string(hide_password=False)))
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/api/core/me")).status_code == 401
        auth = {"Authorization": f"Bearer {token_a}"}
        me = (await c.get("/api/core/me", headers=auth)).json()
        assert me["workspace"]["id"] == str(ws_a)
        members = (await c.get("/api/core/members", headers=auth)).json()
        assert [m["actor_id"] for m in members] == [str(actor_a)]


def test_personal_tables_are_isolated(pg):
    engine = create_engine(pg.owner, poolclass=NullPool)
    with engine.connect() as conn:
        problems = workspace_isolation_problems(
            conn, ["core", "personal"], allow_unforced={("personal", "api_tokens")}
        )
    engine.dispose()
    assert problems == []
