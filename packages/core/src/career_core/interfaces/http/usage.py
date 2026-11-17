"""사용량·비용 조회 경로.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from career_core.ports import ActorContext
from career_core.usecases.usage import UsageRow, usage_summary


class UsageRowOut(BaseModel):
    key: str
    calls: int
    ok: int
    errors: int
    denied: int
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    spent_usd: Decimal
    equivalent_usd: Decimal
    unknown_cost_calls: int


class UsageOut(BaseModel):
    since: datetime
    until: datetime
    group_by: str
    rows: list[UsageRowOut]
    total: UsageRowOut
    month_spent_usd: Decimal
    monthly_budget_usd: Decimal | None
    budget_remaining_usd: Decimal | None
    notes: list[str]


def _out(row: UsageRow) -> UsageRowOut:
    return UsageRowOut.model_validate(row, from_attributes=True)


def add_usage_routes(router: APIRouter, deps, authorize) -> None:
    @router.get("/usage", response_model=UsageOut)
    async def usage(
        actor: Annotated[ActorContext, Depends(authorize("usage.read"))],
        days: Annotated[int, Query(ge=1, le=366)] = 30,
        group_by: Annotated[
            Literal["day", "model", "purpose", "endpoint", "status"], Query()
        ] = "day",
    ) -> UsageOut:
        now = datetime.now(UTC)
        budget = deps.monthly_budget_usd
        gw = deps.models
        if gw is not None and gw.overlay is not None:  # O22: 화면에서 덮은 예산
            budget = (await gw.view(actor.workspace_id)).config.budget.monthly_usd
        summary = await usage_summary(
            deps.db,
            actor.workspace_id,
            since=now - timedelta(days=days),
            until=now,
            group_by=group_by,
            monthly_budget_usd=budget,
            now=now,
        )
        return UsageOut(
            since=summary.since,
            until=summary.until,
            group_by=summary.group_by,
            rows=[_out(r) for r in summary.rows],
            total=_out(summary.total),
            month_spent_usd=summary.month_spent_usd,
            monthly_budget_usd=summary.monthly_budget_usd,
            budget_remaining_usd=summary.budget_remaining_usd,
            notes=summary.notes,
        )
