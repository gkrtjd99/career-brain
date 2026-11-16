"""API 토큰 만료·마지막 사용 시각·웹 관리 경로 (p0002, 합성 공간).

토큰 원문과 해시는 목록·폐기 응답 어디에도 나오지 않는다. 지금 쓰는 토큰은 폐기하지 않는다.
"""

from datetime import timedelta

import httpx
import pytest
from career_core.db import Membership
from career_core.db.base import utcnow
from career_core.usecases.workspaces import create_workspace
from personal_api import cli
from personal_api.identity import (
    ApiToken,
    PersonalTokenResolver,
    hash_token,
    issue_token,
    list_tokens,
)
from personal_api.main import create_app
from personal_api.settings import Settings
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


async def _space(db, name="A"):
    ws, actor = await create_workspace(db, name=name, owner_display_name="me")
    return ws, actor, await issue_token(db, workspace_id=ws, actor_id=actor, label="first")


async def _set(db, ws, token, **values):
    async with db.workspace_session(ws) as s:
        await s.execute(
            update(ApiToken).where(ApiToken.token_hash == hash_token(token)).values(**values)
        )


async def _row(db, ws, token) -> ApiToken:
    async with db.workspace_session(ws) as s:
        return await s.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))


async def test_expired_token_is_rejected(db):
    ws, actor, _ = await _space(db)
    token = await issue_token(db, workspace_id=ws, actor_id=actor, label="short", expires_days=1)
    row = await _row(db, ws, token)
    assert row.expires_at - row.created_at == timedelta(days=1)
    assert (await PersonalTokenResolver(db).resolve(token)).credential_id == row.id
    past = utcnow() - timedelta(days=3)
    await _set(db, ws, token, created_at=past, expires_at=past + timedelta(days=1))
    assert await PersonalTokenResolver(db).resolve(token) is None
    info = {r.label: r for r in await list_tokens(db, workspace_id=ws)}["short"]
    assert info.expired and info.revoked_at is None
    with pytest.raises(ValueError):
        await issue_token(db, workspace_id=ws, actor_id=actor, label="x", expires_days=0)


async def test_last_used_is_written_at_most_once_per_interval(db):
    ws, _, token = await _space(db)
    resolver = PersonalTokenResolver(db)
    assert (await _row(db, ws, token)).last_used_at is None
    await resolver.resolve(token)
    first = (await _row(db, ws, token)).last_used_at
    assert first is not None
    for _ in range(5):
        await resolver.resolve(token)
    assert (await _row(db, ws, token)).last_used_at == first  # 요청마다 쓰지 않는다
    await _set(db, ws, token, last_used_at=utcnow() - timedelta(minutes=6))
    await resolver.resolve(token)
    assert (await _row(db, ws, token)).last_used_at > first


@pytest.fixture
async def client(pg):
    app = create_app(Settings(database_url=pg.app.render_as_string(hide_password=False)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_web_token_list_issue_revoke(db, client):
    _, _, token = await _space(db)
    other_ws, _, other_token = await _space(db, "B")
    h = _auth(token)

    res = await client.get("/api/core/tokens", headers=h)
    assert res.status_code == 200
    [me] = res.json()
    assert (me["label"], me["status"], me["current"]) == ("first", "active", True)
    assert me["last_used_at"] is not None  # 방금 이 요청에 썼다

    res = await client.post(
        "/api/core/tokens", headers=h, json={"label": " phone ", "expires_days": 7}
    )
    assert res.status_code == 201
    assert res.headers["cache-control"] == "no-store"
    issued = res.json()
    secret = issued["secret"]
    assert secret.startswith("cbp_")
    assert issued["token"]["label"] == "phone" and not issued["token"]["current"]
    assert issued["token"]["expires_at"] is not None

    listed = await client.get("/api/core/tokens", headers=h)
    body = listed.text
    # 원문·해시는 목록에 없다 (지금 쓰는 토큰·새 토큰 모두)
    assert "cbp_" not in body and hash_token(token) not in body and hash_token(secret) not in body
    assert "token_hash" not in body
    assert [t["label"] for t in listed.json()] == ["first", "phone"]
    assert (await client.get("/api/core/me", headers=_auth(secret))).status_code == 200

    # 지금 쓰는 토큰은 폐기하지 않는다
    assert (await client.post(f"/api/core/tokens/{me['id']}/revoke", headers=h)).status_code == 409
    # 다른 공간 토큰은 보이지도, 폐기되지도 않는다
    [other] = await list_tokens(db, workspace_id=other_ws)
    assert str(other.id) not in body
    assert (await client.post(f"/api/core/tokens/{other.id}/revoke", headers=h)).status_code == 404
    assert (await client.get("/api/core/me", headers=_auth(other_token))).status_code == 200

    res = await client.post(f"/api/core/tokens/{issued['token']['id']}/revoke", headers=h)
    assert res.status_code == 200 and res.json()["status"] == "revoked"
    assert "cbp_" not in res.text
    assert (await client.get("/api/core/me", headers=_auth(secret))).status_code == 401
    again = await client.post(f"/api/core/tokens/{issued['token']['id']}/revoke", headers=h)
    assert again.status_code == 404

    # 새 토큰으로 바꾼 뒤에는 옛 토큰을 폐기할 수 있다
    second = (await client.post("/api/core/tokens", headers=h, json={"label": "next"})).json()
    res = await client.post(f"/api/core/tokens/{me['id']}/revoke", headers=_auth(second["secret"]))
    assert res.status_code == 200
    assert (await client.get("/api/core/tokens", headers=h)).status_code == 401


async def test_web_token_routes_are_owner_only_and_validate(db, client):
    ws, owner, token = await _space(db)
    async with db.workspace_session(ws) as s:
        m = await s.scalar(select(Membership).where(Membership.actor_id == owner))
        m.role = "editor"
    h = _auth(token)
    assert (await client.get("/api/core/tokens", headers=h)).status_code == 403
    assert (
        await client.post("/api/core/tokens", headers=h, json={"label": "x"})
    ).status_code == 403

    _, _, owner_token = await _space(db, "B")
    h2 = _auth(owner_token)
    for bad in ({"label": ""}, {"label": "x" * 101}, {"label": "x", "expires_days": 0}):
        assert (await client.post("/api/core/tokens", headers=h2, json=bad)).status_code == 422
    assert (
        await client.post("/api/core/tokens", headers=h2, json={"label": "   "})
    ).status_code == 422
    assert (await client.get("/api/core/tokens")).status_code == 401


async def test_cli_issue_with_expiry_and_list_columns(pg, db, monkeypatch, capsys):
    settings = Settings(database_url=pg.app.render_as_string(False))
    token = await cli._bootstrap(settings, "합성 공간", "me")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    assert await cli._token(settings, "issue", label="ci", expires_days=30) == 0
    assert "expires in 30 day(s)" in capsys.readouterr().out
    assert await cli._token(settings, "issue", label="ci", expires_days=0) == 1
    capsys.readouterr()
    assert await cli._token(settings, "list") == 0
    out = capsys.readouterr().out
    assert "expires" in out and "last used" in out
    assert (utcnow() + timedelta(days=30)).strftime("%Y-%m-%d") in out


# --- c0025: 이름 바꾸기·만료 연장·지금 토큰 ---------------------------------------------


async def test_web_token_rename_and_extend(db, client):
    ws, actor, token = await _space(db)
    h = _auth(token)
    short = await issue_token(db, workspace_id=ws, actor_id=actor, label="short", expires_days=3)
    rows = {t["label"]: t for t in (await client.get("/api/core/tokens", headers=h)).json()}
    sid, before = rows["short"]["id"], rows["short"]["expires_at"]

    res = await client.patch(
        f"/api/core/tokens/{sid}", headers=h, json={"label": "  노트북  MCP ", "expires_days": 90}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["label"] == "노트북 MCP" and body["expires_at"] > before
    assert "cbp_" not in res.text and "token_hash" not in res.text
    # 같은 원문이 계속 통한다 (해시를 바꾸지 않는다)
    assert (await client.get("/api/core/me", headers=_auth(short))).status_code == 200
    # 연장만: 앞당기기·만료 없는 토큰에 만료 달기는 409
    res = await client.patch(f"/api/core/tokens/{sid}", headers=h, json={"expires_days": 1})
    assert (res.status_code, res.json()["detail"]) == (409, "not_an_extension")
    me = rows["first"]["id"]
    res = await client.patch(f"/api/core/tokens/{me}", headers=h, json={"expires_days": 30})
    assert (res.status_code, res.json()["detail"]) == (409, "not_an_extension")
    # 지금 쓰는 토큰 이름은 바꿀 수 있다
    res = await client.patch(f"/api/core/tokens/{me}", headers=h, json={"label": "브라우저"})
    assert res.status_code == 200 and res.json()["current"] is True
    # 빈 요청·빈 이름은 422
    for bad in ({}, {"label": "   "}, {"expires_days": 0}, {"label": "x" * 101}):
        assert (
            await client.patch(f"/api/core/tokens/{sid}", headers=h, json=bad)
        ).status_code == 422
    # 폐기·만료된 토큰은 되살리지 않는다
    assert (await client.post(f"/api/core/tokens/{sid}/revoke", headers=h)).status_code == 200
    res = await client.patch(f"/api/core/tokens/{sid}", headers=h, json={"expires_days": 365})
    assert (res.status_code, res.json()["detail"]) == (409, "token_revoked")
    old = await issue_token(db, workspace_id=ws, actor_id=actor, label="old", expires_days=1)
    past = utcnow() - timedelta(days=3)
    await _set(db, ws, old, created_at=past, expires_at=past + timedelta(days=1))
    oid = str((await _row(db, ws, old)).id)
    res = await client.patch(f"/api/core/tokens/{oid}", headers=h, json={"expires_days": 30})
    assert (res.status_code, res.json()["detail"]) == (409, "token_expired")
    assert (await client.get("/api/core/me", headers=_auth(old))).status_code == 401


async def test_web_token_patch_is_owner_only_and_workspace_scoped(db, client):
    ws, owner, token = await _space(db)
    other_ws, _, other_token = await _space(db, "B")
    [other] = await list_tokens(db, workspace_id=other_ws)
    res = await client.patch(
        f"/api/core/tokens/{other.id}", headers=_auth(token), json={"label": "x"}
    )
    assert res.status_code == 404
    [mine] = await list_tokens(db, workspace_id=ws)
    async with db.workspace_session(ws) as s:
        m = await s.scalar(select(Membership).where(Membership.actor_id == owner))
        m.role = "editor"
    res = await client.patch(
        f"/api/core/tokens/{mine.id}", headers=_auth(token), json={"label": "y"}
    )
    assert res.status_code == 403
    # 편집자도 지금 자기 토큰은 볼 수 있다 (오늘 화면의 만료 임박 안내)
    res = await client.get("/api/core/tokens/current", headers=_auth(token))
    assert res.status_code == 200
    assert (res.json()["id"], res.json()["current"]) == (str(mine.id), True)
    assert "cbp_" not in res.text
    assert (await client.get("/api/core/tokens/current")).status_code == 401
    assert (await client.get("/api/core/tokens/current", headers=_auth(other_token))).json()[
        "id"
    ] == str(other.id)
