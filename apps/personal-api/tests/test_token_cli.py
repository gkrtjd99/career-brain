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


# --- token rename · extend (웹·HTTP PATCH /core/tokens/{id}와 같은 update_token) -------------


async def test_rename_and_extend(settings, db, monkeypatch, capsys):
    from datetime import timedelta

    from career_core.db.base import utcnow

    token = await cli._bootstrap(settings, "합성 공간", "me")
    actor = await PersonalTokenResolver(db).resolve(token)
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    assert await cli._token(settings, "issue", label="laptop", expires_days=10) == 0
    laptop = new_token(capsys.readouterr().out)
    rows = await list_tokens(db, workspace_id=actor.workspace_id, current_token=token)
    boot, lap = rows

    # 이름 바꾸기: 공백 정리, 원문은 그대로 통하고 출력에 나오지 않는다
    assert await cli._token(settings, "rename", token_id=str(lap.id), label="  work   laptop ") == 0
    out = capsys.readouterr().out
    assert out == f"renamed {lap.id} to work laptop\n"
    assert laptop not in out and token not in out and "cbp_" not in out
    assert (await PersonalTokenResolver(db).resolve(laptop)) is not None
    # 지금 쓰는 토큰도 이름은 바꿀 수 있다 (폐기와 달리 잠겨 나가지 않는다)
    assert await cli._token(settings, "rename", token_id=str(boot.id), label="main") == 0
    capsys.readouterr()

    # 만료 연장: 지금부터 N일 뒤로, 연장만
    assert await cli._token(settings, "extend", token_id=str(lap.id), expires_days=90) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"extended {lap.id} (work laptop): expires ") and "cbp_" not in out
    rows = await list_tokens(db, workspace_id=actor.workspace_id)
    assert [r.label for r in rows] == ["main", "work laptop"]
    assert abs(rows[1].expires_at - (utcnow() + timedelta(days=90))) < timedelta(minutes=5)
    assert await cli._token(settings, "extend", token_id=str(lap.id), expires_days=30) == 1
    assert "later than the current one" in capsys.readouterr().out
    # 만료가 없는 토큰에 만료를 달지 않는다 (줄이기)
    assert await cli._token(settings, "extend", token_id=str(boot.id), expires_days=30) == 1
    assert "refused" in capsys.readouterr().out
    assert (await list_tokens(db, workspace_id=actor.workspace_id))[0].expires_at is None

    # 폐기한 토큰은 되살리지 않는다
    assert await revoke_token(db, workspace_id=actor.workspace_id, token_id=lap.id)
    assert await cli._token(settings, "extend", token_id=str(lap.id), expires_days=365) == 1
    assert "revoked" in capsys.readouterr().out
    assert await cli._token(settings, "rename", token_id=str(lap.id), label="x") == 1
    assert await PersonalTokenResolver(db).resolve(laptop) is None


async def test_rename_extend_reject_bad_input_and_other_workspaces(
    settings, db, monkeypatch, capsys
):
    mine = await cli._bootstrap(settings, "A", "me")
    other = await cli._bootstrap(settings, "B", "you")
    other_actor = await PersonalTokenResolver(db).resolve(other)
    [other_row] = await list_tokens(db, workspace_id=other_actor.workspace_id)
    monkeypatch.setenv(cli.TOKEN_ENV, mine)
    me = await PersonalTokenResolver(db).resolve(mine)
    [row] = await list_tokens(db, workspace_id=me.workspace_id)

    assert await cli._token(settings, "rename", token_id=str(other_row.id), label="mine") == 1
    assert "no token with that id" in capsys.readouterr().out
    [still] = await list_tokens(db, workspace_id=other_actor.workspace_id)
    assert still.label == "bootstrap"
    assert await cli._token(settings, "rename", token_id="not-a-uuid", label="x") == 1
    assert "rename needs a token id" in capsys.readouterr().out
    assert await cli._token(settings, "rename", token_id=str(row.id), label="   ") == 1
    assert await cli._token(settings, "rename", token_id=str(row.id), label="x" * 101) == 1
    for days in (None, 0, cli.MAX_EXPIRES_DAYS + 1):
        assert await cli._token(settings, "extend", token_id=str(row.id), expires_days=days) == 1
    assert "--days must be" in capsys.readouterr().out
    assert (await list_tokens(db, workspace_id=me.workspace_id))[0].label == "bootstrap"


async def test_rename_extend_need_an_owner_token(settings, db, monkeypatch, capsys):
    ws, owner = await create_workspace(db, name="w", owner_display_name="me")
    async with db.workspace_session(ws) as s:
        m = await s.scalar(select(Membership).where(Membership.actor_id == owner))
        m.role = "editor"
    token = await issue_token(db, workspace_id=ws, actor_id=owner, label="editor")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    [row] = await list_tokens(db, workspace_id=ws)
    assert await cli._token(settings, "rename", token_id=str(row.id), label="owner now") == 1
    assert await cli._token(settings, "extend", token_id=str(row.id), expires_days=30) == 1
    assert "owner" in capsys.readouterr().out
    assert (await list_tokens(db, workspace_id=ws))[0].label == "editor"


def test_rename_extend_subcommands_parse(monkeypatch):
    seen = {}

    async def fake(settings, action, *, token_id=None, label=None, expires_days=None):
        seen.update(action=action, token_id=token_id, label=label, expires_days=expires_days)
        return 0

    monkeypatch.setattr(cli, "_token", fake)
    with pytest.raises(SystemExit) as exc:
        cli.main(["token", "rename", "1234", "new name"])
    assert exc.value.code == 0
    assert seen == {
        "action": "rename",
        "token_id": "1234",
        "label": "new name",
        "expires_days": None,
    }
    with pytest.raises(SystemExit):
        cli.main(["token", "extend", "1234", "--days", "45"])
    assert seen == {"action": "extend", "token_id": "1234", "label": None, "expires_days": 45}
    with pytest.raises(SystemExit) as exc:  # --days는 꼭 준다
        cli.main(["token", "extend", "1234"])
    assert exc.value.code == 2
