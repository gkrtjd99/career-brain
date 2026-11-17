"""O22: 개인 앱은 화면 모델 연결을 켠다. CLI `models`가 화면 연결을 키 값 없이 보여 준다 (합성)."""

import os
import stat

import httpx
import pytest
from career_core.usecases.workspaces import create_workspace
from personal_api import cli
from personal_api.identity import issue_token
from personal_api.main import create_app
from personal_api.models import build_gateway
from personal_api.settings import Settings

pytestmark = pytest.mark.anyio

KEY = "sk-synthetic-TEST-2222-lmno4321"


@pytest.fixture
def settings(pg, tmp_path):
    return Settings(
        database_url=pg.app.render_as_string(hide_password=False),
        models_config=str(tmp_path / "missing-models.toml"),
        secret_key_file=str(tmp_path / "data" / "secret.key"),
        blob_dir=str(tmp_path / "blobs"),
        restore_dir=str(tmp_path / "restore"),
    )


async def test_personal_app_enables_screen_connections(db, settings, monkeypatch, capsys):
    ws, actor = await create_workspace(db, name="A (합성)", owner_display_name="me")
    token = await issue_token(db, workspace_id=ws, actor_id=actor, label="t")
    app = create_app(settings)
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        res = await c.get("/api/core/models/status", headers=h)
        assert res.status_code == 200 and res.json()["connections"] == []
        res = await c.post(
            "/api/core/models/connections",
            json={
                "name": "sub-proxy",
                "preset": "cliproxyapi",
                "api_key": KEY,
                "price_as": "anthropic",
                "data_classes": ["public"],
            },
            headers=h,
        )
        assert res.status_code == 201, res.text
        assert KEY not in res.text
        res = await c.put(
            "/api/core/models/routes",
            json={"routes": {"default": "sub-proxy/claude-sonnet-5-5"}},
            headers=h,
        )
        assert res.status_code == 200, res.text
    assert stat.S_IMODE(os.stat(settings.secret_key_file).st_mode) == 0o600

    # worker·CLI가 만드는 게이트웨이도 같은 설정을 본다 (재시작 없이)
    view = await build_gateway(settings, db).view(ws)
    assert view.config.route_for("anything").endpoint == "sub-proxy"
    assert view.env.get("CAREER_CONN_SUB_PROXY_KEY") == KEY

    monkeypatch.setenv(cli.TOKEN_ENV, token)
    await cli._models(settings)
    out = capsys.readouterr().out
    assert "sub-proxy" in out and "ui:set" in out and "default      ui" in out
    assert KEY not in out and KEY[-4:] not in out
