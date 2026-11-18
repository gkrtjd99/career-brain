"""이 컴퓨터에서 바로 열기 (p0003): bootstrap·토큰 붙여넣기 없이. 합성 공간만 쓴다.

이 컴퓨터(loopback)·이 컴퓨터 이름의 Host·같은 화면에서 보낸 요청만 토큰을 받는다. 나머지는 404.
"""

import httpx
import pytest
from career_core.db import Membership
from career_core.usecases.workspaces import create_workspace
from personal_api.identity import PersonalTokenResolver, issue_token
from personal_api.local_login import TOKEN_LABEL, local_session_token
from personal_api.main import create_app
from personal_api.settings import Settings
from sqlalchemy import select

pytestmark = pytest.mark.anyio

LOCAL = {"X-Career-Local": "1"}


def _client(pg, *, client=("127.0.0.1", 50000), base="http://127.0.0.1:8000"):
    app = create_app(Settings(database_url=pg.app.render_as_string(hide_password=False)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base)


async def test_local_browser_gets_owner_token(db, pg):
    ws, actor = await create_workspace(db, name="합성 공간", owner_display_name="me")
    await issue_token(db, workspace_id=ws, actor_id=actor, label="bootstrap")
    async with _client(pg) as c:
        r = await c.post(
            "/api/local/session",
            headers={**LOCAL, "Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        token = r.json()["token"]
        me = await c.get("/api/core/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
    ident = await PersonalTokenResolver(db).resolve(token)
    async with db.workspace_session(ident.workspace_id) as s:
        role = await s.scalar(
            select(Membership.role).where(Membership.actor_id == ident.subject_id)
        )
    assert role == "owner"


@pytest.mark.parametrize(
    ("kw", "headers"),
    [
        ({}, {}),  # 같은 화면 표시 머리글 없음 (다른 사이트의 단순 POST)
        ({}, {**LOCAL, "Origin": "https://evil.example"}),
        ({}, {**LOCAL, "Sec-Fetch-Site": "cross-site"}),
        ({"client": ("203.0.113.7", 40000)}, LOCAL),  # 다른 기기
        ({"base": "http://career.example.com"}, LOCAL),  # 같은 컴퓨터 proxy가 바깥 주소로 넘김
        ({"base": "http://rebind.example:8000"}, LOCAL),  # DNS rebinding
    ],
)
async def test_other_requests_get_nothing(db, pg, kw, headers):
    async with _client(pg, **kw) as c:
        r = await c.post("/api/local/session", headers=headers)
    assert r.status_code == 404
    assert "token" not in r.text


async def test_can_be_turned_off(db, pg, monkeypatch):
    monkeypatch.setenv("CAREER_LOCAL_LOGIN", "0")
    async with _client(pg) as c:
        assert (await c.post("/api/local/session", headers=LOCAL)).status_code == 404


async def test_prefers_own_space_over_demo(db):
    import asyncio

    mine, me = await create_workspace(db, name="내 공간 (합성)", owner_display_name="me")
    await issue_token(db, workspace_id=mine, actor_id=me, label="bootstrap")
    demo, d = await create_workspace(db, name="데모 (합성)", owner_display_name="demo")
    await issue_token(db, workspace_id=demo, actor_id=d, label="demo")  # 더 최근
    token = await local_session_token(db, asyncio.Lock())
    ident = await PersonalTokenResolver(db).resolve(token)
    assert ident.workspace_id != demo
    # 한 번 연 공간이 다음에도 열린다 (방금 만든 "이 컴퓨터" 토큰이 가장 최근)
    again = await PersonalTokenResolver(db).resolve(await local_session_token(db, asyncio.Lock()))
    assert again.workspace_id == ident.workspace_id
    assert TOKEN_LABEL == "이 컴퓨터"
