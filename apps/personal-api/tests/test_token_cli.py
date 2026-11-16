"""personal-api token list | issue | revoke, maintenance (합성 공간)."""

import pytest
from career_core.db import Membership
from career_core.usecases.workspaces import create_workspace
from personal_api import cli
from personal_api.identity import PersonalTokenResolver, issue_token, list_tokens, revoke_token
from personal_api.settings import Settings
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def new_token(printed: str) -> str:
    return printed.split("store it safely):\n", 1)[1].split("\n", 1)[0]


@pytest.fixture
def settings(pg):
    return Settings(database_url=pg.app.render_as_string(False))


async def test_issue_list_revoke(settings, db, monkeypatch, capsys):
    token = await cli._bootstrap(settings, "합성 공간", "me")
    actor = await PersonalTokenResolver(db).resolve(token)
    monkeypatch.setenv(cli.TOKEN_ENV, token)

    assert await cli._token(settings, "issue", label="laptop") == 0
    second = new_token(capsys.readouterr().out)
    assert second.startswith("cbp_") and second != token
    assert (await PersonalTokenResolver(db).resolve(second)).workspace_id == actor.workspace_id

    assert await cli._token(settings, "list") == 0
    listed = capsys.readouterr().out
    # 토큰 원문(지금 쓰는 것·새로 만든 것)과 해시는 목록에 나오지 않는다
    assert token not in listed and second not in listed and "cbp_" not in listed
    rows = await list_tokens(db, workspace_id=actor.workspace_id, current_token=token)
    assert [(r.label, r.current) for r in rows] == [("bootstrap", True), ("laptop", False)]
    for r in rows:
        assert str(r.id) in listed
    assert f"* {rows[0].id}" in listed

    assert await cli._token(settings, "revoke", token_id=str(rows[1].id)) == 0
    assert "revoked" in capsys.readouterr().out
    assert await PersonalTokenResolver(db).resolve(second) is None
    assert await cli._token(settings, "revoke", token_id=str(rows[1].id)) == 1  # 이미 폐기
    capsys.readouterr()
    assert await cli._token(settings, "list") == 0
    assert "revoked 20" in capsys.readouterr().out


async def test_refuses_to_revoke_the_token_in_use(settings, db, monkeypatch, capsys):
    token = await cli._bootstrap(settings, "합성 공간", "me")
    actor = await PersonalTokenResolver(db).resolve(token)
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    [row] = await list_tokens(db, workspace_id=actor.workspace_id)
    assert await cli._token(settings, "revoke", token_id=str(row.id)) == 1
    assert "refusing" in capsys.readouterr().out
    assert await PersonalTokenResolver(db).resolve(token) is not None


async def test_cannot_revoke_or_see_another_workspace_token(settings, db, monkeypatch, capsys):
    mine = await cli._bootstrap(settings, "A", "me")
    other = await cli._bootstrap(settings, "B", "you")
    other_actor = await PersonalTokenResolver(db).resolve(other)
    [other_row] = await list_tokens(db, workspace_id=other_actor.workspace_id)
    monkeypatch.setenv(cli.TOKEN_ENV, mine)

    assert await cli._token(settings, "list") == 0
    assert str(other_row.id) not in capsys.readouterr().out
    assert await cli._token(settings, "revoke", token_id=str(other_row.id)) == 1
    assert await PersonalTokenResolver(db).resolve(other) is not None
    assert await cli._token(settings, "revoke", token_id="not-a-uuid") == 1


async def test_non_owner_token_is_refused(settings, db, monkeypatch, capsys):
    ws, owner = await create_workspace(db, name="w", owner_display_name="me")
    async with db.workspace_session(ws) as s:
        m = await s.scalar(select(Membership).where(Membership.actor_id == owner))
        m.role = "editor"
    token = await issue_token(db, workspace_id=ws, actor_id=owner, label="editor")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    for action in ("list", "issue"):
        assert await cli._token(settings, action) == 1
    assert "owner" in capsys.readouterr().out
    assert len(await list_tokens(db, workspace_id=ws)) == 1


async def test_issue_rejects_bad_labels(settings, monkeypatch):
    token = await cli._bootstrap(settings, "w", "me")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    assert await cli._token(settings, "issue", label="  ") == 1
    assert await cli._token(settings, "issue", label="x" * 101) == 1


async def test_revoke_token_needs_exactly_one_key(db):
    ws, _ = await create_workspace(db, name="w", owner_display_name="me")
    with pytest.raises(ValueError):
        await revoke_token(db, workspace_id=ws)


async def test_maintenance_command(settings, capsys):
    assert await cli._maintenance(settings) == 0
    assert "maintenance:" in capsys.readouterr().out


def test_token_subcommands_parse(monkeypatch):
    seen = {}

    async def fake(settings, action, *, token_id=None, label=None, expires_days=None):
        seen.update(action=action, token_id=token_id, label=label, expires_days=expires_days)
        return 0

    monkeypatch.setattr(cli, "_token", fake)
    with pytest.raises(SystemExit) as exc:
        cli.main(["token", "issue", "--label", "phone"])
    assert exc.value.code == 0
    assert seen == {"action": "issue", "token_id": None, "label": "phone", "expires_days": None}
    with pytest.raises(SystemExit):
        cli.main(["token", "issue", "--expires-days", "30"])
    assert seen["expires_days"] == 30
    with pytest.raises(SystemExit):
        cli.main(["token", "revoke", "1234"])
    assert seen["action"] == "revoke" and seen["token_id"] == "1234"
