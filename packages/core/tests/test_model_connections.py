"""O22 화면에서 모델 연결·라우트·예산 (plan §21). 합성 키만 쓴다."""

import json
import logging
import os
import stat
import tarfile
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.secret_box import SECRET_KEY_ENV, SecretBox, SecretBoxError
from career_core.db import ModelCall
from career_core.db.models import ModelConnectionSecret
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.llm import (
    ChatMessage,
    ModelCompletion,
    ModelDenied,
    ModelRequest,
    TokenUsage,
    UsagePart,
)
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.model_connections import ModelConnections, key_env_name
from career_core.usecases.workspaces import create_workspace
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
KEY = "sk-synthetic-TEST-0000-wxyz9876"  # 합성 키 (어디에도 유효하지 않다)
KEY2 = "sk-synthetic-TEST-1111-qrst5432"
FILE_CONFIG = parse_models_config("""
[endpoints.filed]
preset = "openai-compatible"
base_url = "http://127.0.0.1:9/v1"
api_key_env = "FILED_KEY"
pricing_provider = "openai"
data_classes = ["public", "private"]

[routes]
default = "filed/gpt-6-astra"

[budget]
monthly_usd = "5"
""")
FILE_ENV = {"FILED_KEY": "file-synthetic-key-0000"}


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


class FakeClient:
    def __init__(self, endpoint, api_key, log):
        self.endpoint, self.api_key, self.log = endpoint, api_key, log

    async def complete(self, model, request):
        self.log.append((self.endpoint.name, model, self.api_key))
        usage = TokenUsage(input_tokens=10, output_tokens=2)
        return ModelCompletion(
            text="ok",
            model=model,
            stop_reason="end_turn",
            parts=(UsagePart(model, usage),),
            request_id="req-synthetic",
        )


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_gateway(db, conns, log, monotonic=None):
    return ModelGateway(
        db,
        FILE_CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: FakeClient(ep, key, log),
        env=FILE_ENV,
        clock=lambda: NOW,
        overlay=conns,
        monotonic=monotonic or Clock(),
    )


def request():
    return ModelRequest(
        messages=(ChatMessage("user", "synthetic"),), data_classes=frozenset({"public"})
    )


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    viewer = ActorContext(a, ws_a, scopes_for_role(Role.VIEWER))
    editor = ActorContext(a, ws_a, scopes_for_role(Role.EDITOR))
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        if req.url.host == "down.invalid":
            raise httpx.ConnectError("synthetic", request=req)
        if req.url.host == "deny.invalid":
            return httpx.Response(401, json={"error": "nope"})
        if req.url.host == "odd.invalid":
            return httpx.Response(200, text="<html>")
        return httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})

    box = SecretBox(key_file=tmp_path / "secret.key", env={})
    conns = ModelConnections(db, box, transport=httpx.MockTransport(handler), clock=lambda: NOW)
    log: list = []
    gw = make_gateway(db, conns, log)
    deps = CoreDeps(
        identity=FakeResolver({"a": owner, "b": other, "v": viewer, "e": editor}),
        db=db,
        models=gw,
        model_connections=conns,
        monthly_budget_usd=FILE_CONFIG.budget.monthly_usd,
    )
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {
            "c": c,
            "owner": owner,
            "other": other,
            "gw": gw,
            "conns": conns,
            "log": log,
            "requests": requests,
            "box": box,
            "tmp": tmp_path,
        }


PROXY = {
    "name": "my-proxy",
    "preset": "openai-compatible",
    "base_url": "https://proxy.example.invalid/v1",
    "api_key": KEY,
    "billing": "subscription",
    "price_as": "anthropic",
    "data_classes": ["public", "private"],
}


def conn(status, name):
    return next(c for c in status["connections"] if c["name"] == name)


async def test_status_shapes_and_crud(env):
    c = env["c"]
    res = await c.get("/core/models/status", headers=auth("a"))
    assert res.status_code == 200, res.text
    s = res.json()
    assert set(s) == {"connections", "routes", "purposes", "budget", "presets"}
    assert s["connections"] == [
        {
            "name": "filed",
            "source": "file",
            "preset": "openai-compatible",
            "kind": "openai",
            "base_url": "http://127.0.0.1:9/v1",
            "billing": "metered",
            "price_as": None,
            "data_classes": ["private", "public"],
            "key": "set",
            "key_last4": None,
            "last_test_at": None,
            "last_test_ok": None,
            "leaves_host": True,
        }
    ]
    assert s["routes"] == [
        {"purpose": "default", "endpoint": "filed", "model": "gpt-6-astra", "source": "file"}
    ]
    assert {"default", "embed", "extract", "judge_claim"} <= set(s["purposes"])
    assert s["budget"] == {"monthly_usd": "5", "source": "file", "spent_usd_this_month": "0"}
    presets = {p["name"]: p for p in s["presets"]}
    assert presets["cliproxyapi"] == {
        "name": "cliproxyapi",
        "kind": "openai",
        "base_url": "http://127.0.0.1:8317/v1",
        "billing": "subscription",
        "needs_key": True,
    }
    assert presets["ollama"]["needs_key"] is False

    res = await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    assert res.status_code == 201, res.text
    made = conn(res.json(), "my-proxy")
    assert made == {
        "name": "my-proxy",
        "source": "ui",
        "preset": "openai-compatible",
        "kind": "openai",
        "base_url": "https://proxy.example.invalid/v1",
        "billing": "subscription",
        "price_as": "anthropic",
        "data_classes": ["private", "public"],
        "key": "set",
        "key_last4": "9876",
        "last_test_at": None,
        "last_test_ok": None,
        "leaves_host": True,
    }
    # 같은 이름은 두 번 못 만든다
    again = await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    assert again.status_code == 409 and again.json()["detail"] == "connection_name_taken"

    # 고치기: 키를 안 보내면 그대로, 보내면 바뀜, null이면 지움
    res = await c.patch(
        "/core/models/connections/my-proxy", json={"data_classes": ["public"]}, headers=auth("a")
    )
    assert res.status_code == 200, res.text
    assert conn(res.json(), "my-proxy")["data_classes"] == ["public"]
    assert conn(res.json(), "my-proxy")["key"] == "set"
    res = await c.patch(
        "/core/models/connections/my-proxy", json={"api_key": KEY2}, headers=auth("a")
    )
    assert conn(res.json(), "my-proxy")["key_last4"] == "5432"
    res = await c.patch(
        "/core/models/connections/my-proxy", json={"api_key": None}, headers=auth("a")
    )
    cleared = conn(res.json(), "my-proxy")
    assert cleared["key"] == "not_needed" and cleared["key_last4"] is None

    # 파일 연결은 읽기 전용
    res = await c.patch("/core/models/connections/filed", json={}, headers=auth("a"))
    assert res.status_code == 409 and res.json()["detail"] == "connection_read_only"
    res = await c.delete("/core/models/connections/filed", headers=auth("a"))
    assert res.status_code == 409
    res = await c.patch("/core/models/connections/nope", json={}, headers=auth("a"))
    assert res.status_code == 404

    # 끊기: 쓰는 라우트가 있으면 409
    res = await c.put(
        "/core/models/routes", json={"routes": {"extract": "my-proxy/m1"}}, headers=auth("a")
    )
    assert res.status_code == 200, res.text
    res = await c.delete("/core/models/connections/my-proxy", headers=auth("a"))
    assert res.status_code == 409 and res.json()["detail"] == "connection_in_use"
    await c.put("/core/models/routes", json={"routes": {"extract": None}}, headers=auth("a"))
    res = await c.delete("/core/models/connections/my-proxy", headers=auth("a"))
    assert res.status_code == 200
    assert [x["name"] for x in res.json()["connections"]] == ["filed"]


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"data_classes": ["public", "restricted"]}, 422, "restricted_not_allowed"),
        ({"data_classes": ["secret"]}, 422, "invalid_data_class"),
        ({"name": "filed"}, 409, "connection_name_taken"),
        ({"name": "Bad Name"}, 422, "invalid_name"),
        ({"preset": "nobody"}, 422, "unknown_preset"),
        ({"base_url": None, "preset": "openai-compatible"}, 422, "base_url_required"),
        ({"base_url": "ftp://x"}, 422, "invalid_base_url"),
        ({"billing": "free-ish"}, 422, "invalid_billing"),
        ({"price_as": "nobody"}, 422, "invalid_price_as"),
        ({"token_param": "n"}, 422, "invalid_token_param"),
        ({"api_key": "has space inside"}, 422, "invalid_api_key"),
        ({"api_key": 1234}, 422, "invalid_api_key"),
        ({"extra": 1}, 422, "unknown_field"),
        ({"preset": "local", "base_url": None, "api_key": KEY}, 422, "key_not_used"),
    ],
)
async def test_validation(env, body, status, code):
    res = await env["c"].post("/core/models/connections", json={**PROXY, **body}, headers=auth("a"))
    assert res.status_code == status, res.text
    assert res.json() == {"detail": code}
    assert KEY not in res.text


async def test_bad_bodies_never_echo_the_key(env):
    c = env["c"]
    raw = '{"name": "x", "api_key": "' + KEY + '",'  # 깨진 JSON
    res = await c.post(
        "/core/models/connections",
        content=raw,
        headers={**auth("a"), "Content-Type": "application/json"},
    )
    assert res.status_code == 422 and KEY not in res.text
    res = await c.post("/core/models/connections", json=[KEY], headers=auth("a"))
    assert res.status_code == 422 and KEY not in res.text


async def test_permissions_and_isolation(env):
    c = env["c"]
    assert (await c.get("/core/models/status", headers=auth("v"))).status_code == 200
    for who in ("v", "e"):
        res = await c.post("/core/models/connections", json=PROXY, headers=auth(who))
        assert res.status_code == 403
        res = await c.put("/core/models/budget", json={"monthly_usd": "1"}, headers=auth(who))
        assert res.status_code == 403
        res = await c.post("/core/models/connections/filed/test", headers=auth(who))
        assert res.status_code == 403
    assert (await c.get("/core/models/status")).status_code == 401

    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    await c.put(
        "/core/models/routes", json={"routes": {"default": "my-proxy/m1"}}, headers=auth("a")
    )
    other = (await c.get("/core/models/status", headers=auth("b"))).json()
    assert [x["source"] for x in other["connections"]] == ["file"]
    assert [r["source"] for r in other["routes"]] == ["file"]
    # B는 A의 연결을 가리킬 수 없다
    res = await c.put(
        "/core/models/routes", json={"routes": {"default": "my-proxy/m1"}}, headers=auth("b")
    )
    assert res.status_code == 422 and res.json()["detail"] == "unknown_endpoint"
    await env["gw"].run(env["other"], "default", request())
    assert env["log"][-1][0] == "filed"


async def test_not_enabled_without_injection(db):
    ws, a = await create_workspace(db, name="S (합성)", owner_display_name="svc")
    owner = ActorContext(a, ws, scopes_for_role(Role.OWNER))
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver({"a": owner}), db=db)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        for method, path in [
            ("GET", "/core/models/status"),
            ("POST", "/core/models/connections"),
            ("PUT", "/core/models/budget"),
        ]:
            res = await c.request(method, path, json={}, headers=auth("a"))
            assert res.status_code == 404 and res.json()["detail"] == "not_enabled"
    # overlay 없는 게이트웨이는 DB를 보지 않는다 (서비스 기본값)
    gw = ModelGateway(db, FILE_CONFIG, PriceBook.load(), env=FILE_ENV)
    assert await gw.view(ws) is gw


async def test_route_override_uses_ui_connection_without_restart(env, caplog):
    c, gw, log, owner = env["c"], env["gw"], env["log"], env["owner"]
    caplog.set_level(logging.DEBUG)
    await gw.run(owner, "extract", request())
    assert log[-1] == ("filed", "gpt-6-astra", "file-synthetic-key-0000")

    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    res = await c.put(
        "/core/models/routes",
        json={"routes": {"extract": "my-proxy/claude-sonnet-5-5"}},
        headers=auth("a"),
    )
    routes = {r["purpose"]: r for r in res.json()["routes"]}
    assert routes["extract"] == {
        "purpose": "extract",
        "endpoint": "my-proxy",
        "model": "claude-sonnet-5-5",
        "source": "ui",
    }
    result = await gw.run(owner, "extract", request())
    assert log[-1] == ("my-proxy", "claude-sonnet-5-5", KEY)
    assert result.endpoint == "my-proxy" and result.cost_basis == "list_price_equivalent"
    # 다른 용도는 파일 기본 라우트 그대로
    await gw.run(owner, "interview", request())
    assert log[-1][0] == "filed"

    # 화면 연결의 data_classes가 egress를 정한다
    await c.patch("/core/models/connections/my-proxy", json={"data_classes": []}, headers=auth("a"))
    with pytest.raises(ModelDenied) as exc:
        await gw.run(owner, "extract", request())
    assert exc.value.code == "egress_denied"
    # 키를 지우면 api_key_missing (합성 env 이름이 os.environ에 생기지 않는다)
    await c.patch(
        "/core/models/connections/my-proxy",
        json={"data_classes": ["public"], "api_key": None},
        headers=auth("a"),
    )
    await gw.run(owner, "extract", request())
    assert log[-1] == ("my-proxy", "claude-sonnet-5-5", None)
    assert key_env_name("my-proxy") not in os.environ

    # 장부·로그 어디에도 키가 없다
    async with env["gw"]._db.workspace_session(owner.workspace_id) as s:
        calls = (await s.execute(select(ModelCall))).scalars().all()
    assert calls and all(KEY not in json.dumps(_row(r), default=str) for r in calls)
    assert KEY not in caplog.text
    # 라우트 null = 파일 값으로
    res = await c.put("/core/models/routes", json={"routes": {"extract": None}}, headers=auth("a"))
    assert all(r["source"] == "file" for r in res.json()["routes"])


def _row(r):
    return {k: v for k, v in vars(r).items() if not k.startswith("_")}


async def test_route_validation(env):
    c = env["c"]
    await c.post(
        "/core/models/connections",
        json={"name": "embedder", "preset": "local", "data_classes": ["private"]},
        headers=auth("a"),
    )
    cases = [
        ({"extract": "embedder/minishlab/potion"}, "local_only_embed"),
        ({"extract": "nowhere/m"}, "unknown_endpoint"),
        ({"extract": "no-slash"}, "invalid_route"),
        ({"Bad": "filed/m"}, "invalid_purpose"),
    ]
    for routes, code in cases:
        res = await c.put("/core/models/routes", json={"routes": routes}, headers=auth("a"))
        assert res.status_code == 422 and res.json()["detail"] == code, routes
    res = await c.put(
        "/core/models/routes",
        json={"routes": {"embed": "embedder/minishlab/potion"}},
        headers=auth("a"),
    )
    assert res.status_code == 200
    local = conn(res.json(), "embedder")
    assert local["leaves_host"] is False and local["key"] == "not_needed"
    res = await c.put("/core/models/routes", json={"nope": {}}, headers=auth("a"))
    assert res.status_code == 422


async def test_budget_override_and_usage(env):
    c = env["c"]
    res = await c.put("/core/models/budget", json={"monthly_usd": "20.00"}, headers=auth("a"))
    assert res.status_code == 200, res.text
    assert res.json()["budget"]["monthly_usd"] == "20" and res.json()["budget"]["source"] == "ui"
    usage = (await c.get("/core/usage", headers=auth("a"))).json()
    assert usage["monthly_budget_usd"] == "20.0000"
    assert (await c.get("/core/usage", headers=auth("b"))).json()["monthly_budget_usd"] == "5"
    view = await env["gw"].view(env["owner"].workspace_id)
    assert view.config.budget.monthly_usd == Decimal(20)
    for bad in ("-1", "abc", True, "1e9"):
        res = await c.put("/core/models/budget", json={"monthly_usd": bad}, headers=auth("a"))
        assert res.status_code == 422 and res.json()["detail"] == "invalid_budget", bad
    res = await c.put("/core/models/budget", json={"monthly_usd": None}, headers=auth("a"))
    assert res.json()["budget"] == {
        "monthly_usd": "5",
        "source": "file",
        "spent_usd_this_month": "0",
    }


async def test_worker_process_picks_up_changes_by_version(env, db):
    """다른 프로세스(worker)의 게이트웨이: invalidate를 받지 못해도 refresh 간격 뒤에 다시 읽는다."""
    c, owner = env["c"], env["owner"]
    clock = Clock()
    worker_log: list = []
    worker = make_gateway(db, env["conns"], worker_log, monotonic=clock)
    await worker.run(owner, "extract", request())
    assert worker_log[-1][0] == "filed"
    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    await c.put(
        "/core/models/routes", json={"routes": {"extract": "my-proxy/m1"}}, headers=auth("a")
    )
    await worker.run(owner, "extract", request())
    assert worker_log[-1][0] == "filed"  # 아직 간격 안
    clock.t += 5
    await worker.run(owner, "extract", request())
    assert worker_log[-1] == ("my-proxy", "m1", KEY)
    # 같은 서명이면 다시 만들지 않는다 (클라이언트도 그대로)
    view = await worker.view(owner.workspace_id)
    clock.t += 5
    assert await worker.view(owner.workspace_id) is view
    # reconfigure: 파일 설정을 바꾸면 공간 보기도 새로 만든다
    worker.reconfigure(parse_models_config(""), {})
    with pytest.raises(ModelDenied):
        await worker.run(owner, "interview", request())  # 파일 default 라우트가 사라졌다


async def test_connection_test_only_lists_models(env, db):
    c, reqs, owner = env["c"], env["requests"], env["owner"]
    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    res = await c.post("/core/models/connections/my-proxy/test", headers=auth("a"))
    assert res.status_code == 200
    assert res.json() == {"ok": True, "models": ["model-a", "model-b"]}
    assert KEY not in res.text
    [req] = reqs
    assert req.method == "GET" and str(req.url) == "https://proxy.example.invalid/v1/models"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    s = (await c.get("/core/models/status", headers=auth("a"))).json()
    tested = conn(s, "my-proxy")
    assert tested["last_test_ok"] is True and tested["last_test_at"].startswith("2026-11-17T12")

    # Anthropic: GET /v1/models, x-api-key + anthropic-version
    await c.post(
        "/core/models/connections",
        json={"name": "claude", "preset": "anthropic", "api_key": KEY2, "data_classes": []},
        headers=auth("a"),
    )
    res = await c.post("/core/models/connections/claude/test", headers=auth("a"))
    assert res.json()["ok"] is True
    req = reqs[-1]
    assert req.method == "GET" and req.url.path == "/v1/models"
    assert req.url.host == "api.anthropic.com"
    assert req.headers["x-api-key"] == KEY2 and req.headers["anthropic-version"]
    assert "authorization" not in req.headers

    # 파일 연결도 env 키로 시험할 수 있다
    res = await c.post("/core/models/connections/filed/test", headers=auth("a"))
    assert res.json()["ok"] is True
    assert reqs[-1].headers["authorization"] == "Bearer file-synthetic-key-0000"

    # 오류 코드
    for host, code in [
        ("down.invalid", "unreachable"),
        ("deny.invalid", "unauthorized"),
        ("odd.invalid", "bad_response"),
    ]:
        name = host.split(".")[0]
        await c.post(
            "/core/models/connections",
            json={**PROXY, "name": name, "base_url": f"https://{host}/v1"},
            headers=auth("a"),
        )
        res = await c.post(f"/core/models/connections/{name}/test", headers=auth("a"))
        assert res.json() == {"ok": False, "models": [], "error_code": code}
        assert KEY not in res.text
    await c.post(
        "/core/models/connections",
        json={"name": "nokey", "preset": "xai", "data_classes": ["public"]},
        headers=auth("a"),
    )
    n = len(reqs)
    res = await c.post("/core/models/connections/nokey/test", headers=auth("a"))
    assert res.json() == {"ok": False, "models": [], "error_code": "key_missing"}
    assert len(reqs) == n  # 키가 없으면 부르지 않는다
    assert all(r.method == "GET" and r.url.path.endswith("/models") for r in reqs)
    # 시험은 과금·장부 0
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ModelCall)) == 0
    res = await c.post("/core/models/connections/missing/test", headers=auth("a"))
    assert res.status_code == 404


async def test_export_has_no_key_and_restore_needs_reentry(env, db, tmp_path):
    c, owner = env["c"], env["owner"]
    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    await c.put(
        "/core/models/routes", json={"routes": {"extract": "my-proxy/m1"}}, headers=auth("a")
    )
    await c.put("/core/models/budget", json={"monthly_usd": "7"}, headers=auth("a"))
    async with db.workspace_session(owner.workspace_id) as s:
        [cipher] = (await s.execute(select(ModelConnectionSecret.ciphertext))).scalars().all()
    assert KEY.encode() not in bytes(cipher)

    blobs = LocalBlobStore(tmp_path / "blobs")
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
        data = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
    assert "tables/core.model_connections.jsonl" in names
    assert "tables/core.model_connection_secrets.jsonl" not in names
    assert KEY.encode() not in data and bytes(cipher).hex().encode() not in data
    assert b"9876" in data  # 끝 4자리는 남는다

    copy = await restore_workspace(db, blobs, out, new_ids=True)
    [new_owner] = copy.owners
    restored = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    s = await env["conns"].status(restored, FILE_CONFIG, FILE_ENV)
    proxy = conn(s, "my-proxy")
    assert proxy["key"] == "needs_reentry" and proxy["key_last4"] == "9876"
    assert {r["purpose"]: r["source"] for r in s["routes"]}["extract"] == "ui"
    assert s["budget"]["monthly_usd"] == "7"
    # 키가 없으니 호출은 막힌다
    with pytest.raises(ModelDenied) as exc:
        await env["gw"].run(restored, "extract", request())
    assert exc.value.code == "api_key_missing"


async def test_other_secret_key_means_reentry(env, db, tmp_path):
    c, owner = env["c"], env["owner"]
    await c.post("/core/models/connections", json=PROXY, headers=auth("a"))
    moved = ModelConnections(db, SecretBox(key=Fernet.generate_key(), env={}))
    s = await moved.status(owner, FILE_CONFIG, FILE_ENV)
    assert conn(s, "my-proxy")["key"] == "needs_reentry"


def test_secret_box_key_sources(tmp_path, monkeypatch):
    path = tmp_path / "data" / "secret.key"
    box = SecretBox(key_file=path, env={})
    assert box.decrypt(b"x") is None and not path.exists()  # 풀기만으로는 만들지 않는다
    token = box.encrypt("synthetic-value")
    assert path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert SecretBox(key_file=path, env={}).decrypt(token) == "synthetic-value"
    assert "synthetic" not in repr(box) and path.read_text().strip() not in repr(box)
    key = Fernet.generate_key().decode()
    from_env = SecretBox(key_file=tmp_path / "unused.key", env={SECRET_KEY_ENV: key})
    t2 = from_env.encrypt("v")
    assert not (tmp_path / "unused.key").exists()
    assert SecretBox(key=key).decrypt(t2) == "v"
    assert SecretBox(key_file=path, env={}).decrypt(t2) is None  # 다른 암호 키
    with pytest.raises(SecretBoxError) as exc:
        SecretBox(env={SECRET_KEY_ENV: "not-a-key-synthetic"})
    assert "not-a-key-synthetic" not in str(exc.value)
    with pytest.raises(SecretBoxError):
        SecretBox(env={}).encrypt("v")
