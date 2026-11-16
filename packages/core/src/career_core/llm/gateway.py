"""모델 게이트웨이: 모든 LLM 호출이 지나는 한 곳.

순서: 라우트 → egress(자료 등급) → 가격 확인 → 월 예산 → 호출 → 비용 계산 → 장부 기록.
호출 전에 막힌 것도 `denied`로 장부에 남긴다 (모니터링용). 프롬프트 본문은 남기지 않는다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from career_core.db import Database, ModelCall
from career_core.llm.config import (
    BILLING_METERED,
    EndpointConfig,
    ModelsConfig,
    Route,
)
from career_core.llm.pricing import Cost, PriceBook
from career_core.llm.types import (
    ModelCallError,
    ModelCompletion,
    ModelDenied,
    ModelRequest,
    ModelResult,
)
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext, BudgetPolicy, EgressPolicy, ModelClient

ClientFactory = Callable[[EndpointConfig, str | None], ModelClient]
BUDGET_KIND = "llm_usd_micro"  # BudgetPolicy에 넘기는 단위: 백만분의 1 달러


def default_client_factory(endpoint: EndpointConfig, api_key: str | None) -> ModelClient:
    if endpoint.kind == "anthropic":
        from career_core.adapters.llm_anthropic import AnthropicModelClient

        return AnthropicModelClient(endpoint, api_key)
    from career_core.adapters.llm_openai import OpenAICompatibleClient

    return OpenAICompatibleClient(endpoint, api_key)


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_spend(db: Database, workspace_id: UUID, now: datetime) -> Decimal:
    """이번 달(UTC) 종량제 실지출. 구독·무료 엔드포인트의 환산액은 넣지 않는다."""
    async with db.workspace_session(workspace_id) as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(ModelCall.cost_usd), 0)).where(
                ModelCall.billing == BILLING_METERED,
                ModelCall.created_at >= month_start(now),
            )
        )
    return Decimal(total or 0)


@dataclass(frozen=True, slots=True)
class _Plan:
    route: Route
    endpoint: EndpointConfig
    price_provider: str | None


class ModelGateway:
    def __init__(
        self,
        db: Database,
        config: ModelsConfig,
        prices: PriceBook,
        *,
        egress: EgressPolicy | None = None,
        budget: BudgetPolicy | None = None,
        client_factory: ClientFactory = default_client_factory,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._config = config
        self._prices = prices
        self._egress = egress or ProviderEgressPolicy(config.egress_map())
        self._budget = budget
        self._factory = client_factory
        self._env = os.environ if env is None else env
        self._clock = clock
        self._clients: dict[str, ModelClient] = {}

    @property
    def config(self) -> ModelsConfig:
        return self._config

    def _client(self, endpoint: EndpointConfig, api_key: str | None) -> ModelClient:
        client = self._clients.get(endpoint.name)
        if client is None:
            client = self._factory(endpoint, api_key)
            self._clients[endpoint.name] = client
        return client

    async def run(self, actor: ActorContext, purpose: str, request: ModelRequest) -> ModelResult:
        route = self._config.route_for(purpose)
        if route is None:
            raise ModelDenied("no_route")
        endpoint = self._config.endpoints[route.endpoint]
        metered = endpoint.billing == BILLING_METERED
        plan = _Plan(route, endpoint, endpoint.pricing_provider if metered else endpoint.price_as)

        denial = await self._precheck(actor, purpose, request, plan)
        if denial:
            await self._record(actor, purpose, plan, status="denied", error_code=denial)
            raise ModelDenied(denial)

        api_key = self._env.get(endpoint.api_key_env) if endpoint.api_key_env else None
        if endpoint.api_key_env and not api_key:
            await self._record(actor, purpose, plan, status="denied", error_code="api_key_missing")
            raise ModelDenied("api_key_missing")

        reservation = await self._reserve(actor, purpose, request, plan) if metered else None
        started = time.perf_counter()
        try:
            completion = await self._client(endpoint, api_key).complete(route.model, request)
        except ModelCallError as exc:
            await self._release(reservation)
            await self._record(
                actor, purpose, plan, status="error", error_code=exc.code, latency=started
            )
            raise
        except Exception as exc:
            await self._release(reservation)
            await self._record(
                actor,
                purpose,
                plan,
                status="error",
                error_code=type(exc).__name__,
                latency=started,
            )
            raise

        cost, basis = self._cost(plan, completion)
        call_id = await self._record(
            actor,
            purpose,
            plan,
            status="ok",
            completion=completion,
            cost=cost,
            basis=basis,
            latency=started,
        )
        if reservation is not None and self._budget is not None:
            actual = int((cost.usd or Decimal(0)) * 1_000_000) if basis != "unknown" else 0
            await self._budget.settle(reservation, actual)
        return ModelResult(
            text=completion.text,
            endpoint=endpoint.name,
            model=completion.model,
            stop_reason=completion.stop_reason,
            usage=completion.usage,
            cost_usd=str(cost.usd) if cost.usd is not None else None,
            cost_basis=basis,
            call_id=str(call_id),
            notes=cost.notes,
        )

    async def _precheck(
        self, actor: ActorContext, purpose: str, request: ModelRequest, plan: _Plan
    ) -> str | None:
        if not request.data_classes:
            return "data_class_missing"
        for data_class in request.data_classes:
            if not self._egress.allows(actor, plan.endpoint.name, data_class, purpose):
                return "egress_denied"
        if plan.endpoint.billing != BILLING_METERED:
            return None
        budget = self._config.budget
        if budget.require_known_price and (
            plan.price_provider is None
            or self._prices.lookup(plan.price_provider, plan.route.model) is None
        ):
            return "price_unknown"
        if budget.monthly_usd is not None:
            spent = await month_spend(self._db, actor.workspace_id, self._clock())
            if spent >= budget.monthly_usd:
                return "budget_exceeded"
        return None

    def _estimate_micro_usd(self, request: ModelRequest, plan: _Plan) -> int:
        price = (
            self._prices.lookup(plan.price_provider, plan.route.model)
            if plan.price_provider
            else None
        )
        if price is None:
            return 0
        # 상한 추정: 글자 수를 토큰 수로 본다 (한국어는 대략 이 근처, 영어는 과대 추정).
        prompt_chars = len(request.system or "") + sum(len(m.content) for m in request.messages)
        rates = price.rates_for(prompt_chars, self._clock().date())
        usd = (prompt_chars * rates.input + request.max_output_tokens * rates.output) / 1_000_000
        return int(usd * 1_000_000) + 1

    async def _reserve(
        self, actor: ActorContext, purpose: str, request: ModelRequest, plan: _Plan
    ) -> str | None:
        if self._budget is None:
            return None
        key = str(uuid4())
        reservation = await self._budget.reserve(
            actor, BUDGET_KIND, self._estimate_micro_usd(request, plan), key
        )
        if reservation is None:
            await self._record(actor, purpose, plan, status="denied", error_code="budget_exceeded")
            raise ModelDenied("budget_exceeded")
        return reservation

    async def _release(self, reservation: str | None) -> None:
        if reservation is not None and self._budget is not None:
            await self._budget.release(reservation)

    def _cost(self, plan: _Plan, completion: ModelCompletion) -> tuple[Cost, str]:
        metered = plan.endpoint.billing == BILLING_METERED
        if metered and completion.reported_cost_usd is not None:
            return Cost(completion.reported_cost_usd, ("provider_reported",)), "provider_reported"
        if not completion.parts:
            return Cost(None, (), ("usage_missing",)), "unknown" if metered else "none"
        if plan.price_provider is None:
            return Cost(None), "unknown" if metered else "none"
        cost = self._prices.cost(plan.price_provider, completion.parts, on=self._clock().date())
        if not cost.known:
            return cost, "unknown" if metered else "none"
        if not metered:
            return cost, "list_price_equivalent"
        fell_back = len(completion.parts) > 1 or completion.model != plan.route.model
        if cost.notes or fell_back:
            # 폴백 시도는 크레딧으로 재조정될 수 있어 상한값이다. 실제 청구는 제공자 콘솔이 기준.
            return cost, "estimated"
        return cost, "metered"

    async def _record(
        self,
        actor: ActorContext,
        purpose: str,
        plan: _Plan,
        *,
        status: str,
        error_code: str | None = None,
        completion: ModelCompletion | None = None,
        cost: Cost | None = None,
        basis: str | None = None,
        latency: float | None = None,
    ) -> UUID:
        usage = completion.usage if completion else None
        row = ModelCall(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            actor_id=actor.subject_id,
            purpose=purpose,
            endpoint=plan.endpoint.name,
            pricing_provider=plan.price_provider,
            requested_model=plan.route.model,
            served_model=completion.model if completion else None,
            status=status,
            error_code=error_code,
            stop_reason=completion.stop_reason if completion else None,
            input_tokens=usage.input_tokens if usage else 0,
            cached_input_tokens=usage.cached_input_tokens if usage else 0,
            cache_write_tokens=(usage.cache_write_tokens + usage.cache_write_long_tokens)
            if usage
            else 0,
            output_tokens=usage.output_tokens if usage else 0,
            reasoning_tokens=usage.reasoning_tokens if usage else 0,
            billing=plan.endpoint.billing,
            cost_usd=cost.usd if cost else None,
            cost_basis=basis or "none",
            price_refs=list(cost.refs) if cost else [],
            latency_ms=int((time.perf_counter() - latency) * 1000) if latency else None,
            provider_request_id=completion.request_id if completion else None,
            created_at=self._clock(),
        )
        async with self._db.workspace_session(actor.workspace_id) as session:
            session.add(row)
        return row.id
