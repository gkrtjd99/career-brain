"""게이트웨이: egress·가격·예산 차단, 장부 기록, 사용량 집계, 공간 격리."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from career_core.db import ModelCall
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.llm import (
    ChatMessage,
    ModelCallError,
    ModelCompletion,
    ModelDenied,
    ModelRequest,
    TokenUsage,
    UsagePart,
)
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway, month_spend
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.testing import TEST_APP_LOGIN
from career_core.usecases.usage import usage_summary
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
CONFIG = parse_models_config("""
[endpoints.claude]
preset = "anthropic"
data_classes = ["public", "private", "restricted"]

[endpoints.grok]
preset = "xai"
data_classes = ["public"]

[endpoints.proxy]
preset = "cliproxyapi"
price_as = "anthropic"
data_classes = ["public", "private"]

[endpoints.odd]
preset = "openai-compatible"
base_url = "https://example.invalid/v1"
api_key_env = "ODD_KEY"
pricing_provider = "nobody"
data_classes = ["public"]

[routes]
default = "claude/claude-opus-5-5"
grok = "grok/grok-4.7"
proxy = "proxy/claude-sonnet-5-5"
odd = "odd/mystery-1"

[budget]
monthly_usd = "1"
""")
ENV = {"ANTHROPIC_API_KEY": "k", "XAI_API_KEY": "k", "CLIPROXY_API_KEY": "k", "ODD_KEY": "k"}


class FakeClient:
    def __init__(self, usage=None, *, error=None, reported=None, served=None):
        self.usage = usage or TokenUsage(input_tokens=1000, output_tokens=100)
        self.error = error
        self.reported = reported
        self.served = served
        self.calls = []

    async def complete(self, model, request):
        self.calls.append((model, request))
        if self.error:
            raise self.error
        return ModelCompletion(
            text="ok",
            model=self.served or model,
            stop_reason="end_turn",
            parts=(UsagePart(self.served or model, self.usage),),
            request_id="req-1",
            reported_cost_usd=self.reported,
        )


def request(*classes):
    return ModelRequest(
        messages=(ChatMessage("user", "synthetic"),), data_classes=frozenset(classes)
    )


@pytest.fixture
async def actors(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = scopes_for_role(Role.OWNER)
    return ActorContext(a, ws_a, owner), ActorContext(b, ws_b, owner)


def gateway(db, client, *, env=ENV, config=CONFIG):
    return ModelGateway(
        db,
        config,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env=env,
        clock=lambda: NOW,
    )


async def rows(db, ws):
    async with db.workspace_session(ws) as s:
        return (await s.execute(select(ModelCall).order_by(ModelCall.created_at))).scalars().all()


async def test_ok_call_is_priced_and_recorded(db, actors):
    a, b = actors
    client = FakeClient()
    result = await gateway(db, client).run(a, "interview", request("private"))
    # opus 5.5: 1000 * 4 + 100 * 20 = 6000 / 1M
    assert result.cost_usd == "0.006" and result.cost_basis == "metered"
    assert client.calls[0][0] == "claude-opus-5-5"
    (row,) = await rows(db, a.workspace_id)
    assert (row.status, row.purpose, row.endpoint, row.billing) == (
        "ok",
        "interview",
        "claude",
        "metered",
    )
    assert row.cost_usd == Decimal("0.006")
    assert row.price_refs == ["anthropic/claude-opus-5-5@2026-11-16"]
    assert await rows(db, b.workspace_id) == []  # 다른 공간에서는 안 보인다


async def test_restricted_never_leaves_even_if_configured(db, actors):
    a, _ = actors
    client = FakeClient()
    with pytest.raises(ModelDenied, match="egress_denied"):
        await gateway(db, client).run(a, "default", request("private", "restricted"))
    with pytest.raises(ModelDenied, match="data_class_missing"):
        await gateway(db, client).run(a, "default", request())
    with pytest.raises(ModelDenied, match="egress_denied"):
        await gateway(db, client).run(a, "grok", request("private"))
    assert client.calls == []
    statuses = [(r.status, r.error_code) for r in await rows(db, a.workspace_id)]
    assert statuses == [
        ("denied", "egress_denied"),
        ("denied", "data_class_missing"),
        ("denied", "egress_denied"),
    ]


async def test_unknown_price_is_not_called(db, actors):
    a, _ = actors
    client = FakeClient()
    with pytest.raises(ModelDenied, match="price_unknown"):
        await gateway(db, client).run(a, "odd", request("public"))
    assert client.calls == []


async def test_missing_key_denied(db, actors):
    a, _ = actors
    with pytest.raises(ModelDenied, match="api_key_missing"):
        await gateway(db, FakeClient(), env={}).run(a, "default", request("public"))


async def test_monthly_budget_blocks_after_spend(db, actors):
    a, b = actors
    big = FakeClient(TokenUsage(input_tokens=100_000, output_tokens=40_000))  # 0.4 + 0.8 = 1.2
    gw = gateway(db, big)
    await gw.run(a, "default", request("public"))
    assert await month_spend(db, a.workspace_id, NOW) == Decimal("1.2")
    with pytest.raises(ModelDenied, match="budget_exceeded"):
        await gw.run(a, "default", request("public"))
    await gw.run(b, "default", request("public"))  # 예산은 공간별


async def test_subscription_proxy_is_equivalent_cost_not_spend(db, actors):
    a, _ = actors
    result = await gateway(db, FakeClient()).run(a, "proxy", request("private"))
    # sonnet 5.5 환산: 1000 * 2 + 100 * 10 = 3000 / 1M
    assert (result.cost_usd, result.cost_basis) == ("0.003", "list_price_equivalent")
    assert await month_spend(db, a.workspace_id, NOW) == 0


async def test_provider_reported_cost_wins(db, actors):
    a, _ = actors
    result = await gateway(db, FakeClient(reported=Decimal("0.0042"))).run(
        a, "grok", request("public")
    )
    assert (result.cost_usd, result.cost_basis) == ("0.0042", "provider_reported")


async def test_fallback_served_by_other_model_is_estimated(db, actors):
    a, _ = actors
    result = await gateway(db, FakeClient(served="claude-opus-5")).run(
        a, "default", request("public")
    )
    assert result.cost_basis == "estimated" and result.model == "claude-opus-5"


async def test_provider_error_recorded_and_raised(db, actors):
    a, _ = actors
    client = FakeClient(error=ModelCallError("rate_limited", retryable=True))
    with pytest.raises(ModelCallError, match="rate_limited"):
        await gateway(db, client).run(a, "default", request("public"))
    (row,) = await rows(db, a.workspace_id)
    assert (row.status, row.error_code, row.cost_usd) == ("error", "rate_limited", None)


async def test_ledger_is_append_only_for_app_role(db, actors):
    a, _ = actors
    await gateway(db, FakeClient()).run(a, "default", request("public"))
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.workspace_session(a.workspace_id) as s:
            await s.execute(update(ModelCall).values(cost_usd=0))
    async with db.workspace_session(a.workspace_id) as s:
        assert (await s.execute(text("select current_user"))).scalar() == TEST_APP_LOGIN


async def test_usage_summary_and_http(db, actors):
    a, b = actors
    gw = gateway(db, FakeClient())
    await gw.run(a, "default", request("public"))
    await gw.run(a, "proxy", request("public"))
    with pytest.raises(ModelDenied):
        await gw.run(a, "odd", request("public"))

    s = await usage_summary(
        db,
        a.workspace_id,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        group_by="endpoint",
        monthly_budget_usd=Decimal(1),
        now=NOW,
    )
    by = {r.key: r for r in s.rows}
    assert by["claude"].spent_usd == Decimal("0.006")
    assert by["proxy"].spent_usd == 0 and by["proxy"].equivalent_usd == Decimal("0.003")
    assert by["odd"].denied == 1
    assert (s.total.calls, s.total.input_tokens) == (3, 2000)
    assert s.budget_remaining_usd == Decimal("0.994")

    class Tokens:
        async def resolve(self, credential):
            return {"a": a, "b": b}.get(credential)

    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens(), db=db, monthly_budget_usd=Decimal(1)))
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        res = await c.get(
            "/core/usage?group_by=model&days=366", headers={"Authorization": "Bearer a"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["monthly_budget_usd"] == "1"
        other = await c.get("/core/usage", headers={"Authorization": "Bearer b"})
        assert other.json()["total"]["calls"] == 0
        assert (await c.get("/core/usage")).status_code == 401
