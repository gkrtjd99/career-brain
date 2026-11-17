"""개인 앱의 MCP: 같은 API 토큰, 폐기하면 바로 막힘, worker 작업 종류에 실행(run) 포함."""

import httpx
import pytest
from career_core.domain.runs import RUN_JOB
from career_core.usecases.workspaces import create_workspace
from personal_api.cli import build_handlers
from personal_api.identity import issue_token, revoke_token
from personal_api.main import create_app
from personal_api.settings import Settings

pytestmark = pytest.mark.anyio


async def test_mcp_uses_personal_tokens_and_revocation(pg, db):
    ws, actor = await create_workspace(db, name="A", owner_display_name="a")
    token = await issue_token(db, workspace_id=ws, actor_id=actor, label="mcp")
    settings = Settings(database_url=pg.app.render_as_string(hide_password=False))
    app = create_app(settings)
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "list_experiences", "arguments": {}},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        assert (await c.post("/api/core/mcp", json=msg)).status_code == 401
        auth = {"Authorization": f"Bearer {token}"}
        res = await c.post("/api/core/mcp", json=msg, headers=auth)
        assert res.json()["result"]["structuredContent"] == {"experiences": [], "total": 0}
        assert await revoke_token(db, workspace_id=ws, token=token)
        assert (await c.post("/api/core/mcp", json=msg, headers=auth)).status_code == 401
    assert RUN_JOB in build_handlers(settings, db)
