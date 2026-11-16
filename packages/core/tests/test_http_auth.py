"""O02 완료 기준: 누락된 인증 차단, 공간 범위는 resolver가 확정."""

import httpx
import pytest
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI

pytestmark = pytest.mark.anyio


class FakeResolver:
    def __init__(self, tokens: dict[str, ActorContext]):
        self.tokens = tokens

    async def resolve(self, credential: str) -> ActorContext | None:
        return self.tokens.get(credential)


@pytest.fixture
async def setup(db):
    ws_a, actor_a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, actor_b = await create_workspace(db, name="B", owner_display_name="bob")
    tokens = {
        "tok-a": ActorContext(actor_a, ws_a, scopes_for_role(Role.OWNER)),
        "tok-noscope": ActorContext(actor_a, ws_a, frozenset()),
    }
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c, (ws_a, actor_a), (ws_b, actor_b)


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}]
)
async def test_missing_or_bad_credentials_are_rejected(setup, headers):
    client, *_ = setup
    for path in ("/core/me", "/core/members"):
        res = await client.get(path, headers=headers)
        assert res.status_code == 401, (path, res.status_code)


async def test_me_is_bound_to_resolved_workspace(setup):
    client, (ws_a, actor_a), (ws_b, _) = setup
    res = await client.get(
        "/core/me", params={"workspace_id": str(ws_b)}, headers={"Authorization": "Bearer tok-a"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["workspace"] == {"id": str(ws_a), "name": "A"}
    assert body["actor_id"] == str(actor_a)


async def test_members_only_from_own_workspace(setup):
    client, (_, actor_a), _ = setup
    res = await client.get("/core/members", headers={"Authorization": "Bearer tok-a"})
    assert [m["actor_id"] for m in res.json()] == [str(actor_a)]


async def test_missing_scope_is_forbidden(setup):
    client, *_ = setup
    res = await client.get("/core/members", headers={"Authorization": "Bearer tok-noscope"})
    assert res.status_code == 403


def test_router_without_deps_exposes_only_health():
    paths = {r.path for r in build_router().routes}
    assert paths == {"/core/health"}
