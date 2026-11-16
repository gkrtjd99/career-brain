"""모델 사용량·비용 집계 (모니터링, 대시보드용)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select

from career_core.db import Database, ModelCall
from career_core.llm.config import BILLING_METERED
from career_core.llm.gateway import month_spend

GROUPS = {
    "day": func.date_trunc("day", func.timezone("UTC", ModelCall.created_at)),
    "model": func.coalesce(ModelCall.served_model, ModelCall.requested_model),
    "purpose": ModelCall.purpose,
    "endpoint": ModelCall.endpoint,
    "status": ModelCall.status,
}


@dataclass(frozen=True, slots=True)
class UsageRow:
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
    spent_usd: Decimal  # 종량제 실지출 (가격을 아는 호출만)
    equivalent_usd: Decimal  # 구독·무료 엔드포인트를 API 정가로 환산한 값
    unknown_cost_calls: int  # 종량제인데 비용을 못 센 호출 (단가 미상 등)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    since: datetime
    until: datetime
    group_by: str
    rows: list[UsageRow]
    total: UsageRow
    month_spent_usd: Decimal
    monthly_budget_usd: Decimal | None
    notes: list[str] = field(default_factory=list)

    @property
    def budget_remaining_usd(self) -> Decimal | None:
        if self.monthly_budget_usd is None:
            return None
        return max(self.monthly_budget_usd - self.month_spent_usd, Decimal(0))


def _row(key: str, r) -> UsageRow:
    return UsageRow(
        key=key,
        calls=r.calls or 0,
        ok=r.ok or 0,
        errors=r.errors or 0,
        denied=r.denied or 0,
        input_tokens=r.input_tokens or 0,
        cached_input_tokens=r.cached_input_tokens or 0,
        cache_write_tokens=r.cache_write_tokens or 0,
        output_tokens=r.output_tokens or 0,
        reasoning_tokens=r.reasoning_tokens or 0,
        spent_usd=Decimal(r.spent_usd or 0),
        equivalent_usd=Decimal(r.equivalent_usd or 0),
        unknown_cost_calls=r.unknown_cost_calls or 0,
    )


def _measures():
    metered = ModelCall.billing == BILLING_METERED
    return (
        func.count().label("calls"),
        func.count().filter(ModelCall.status == "ok").label("ok"),
        func.count().filter(ModelCall.status == "error").label("errors"),
        func.count().filter(ModelCall.status == "denied").label("denied"),
        func.sum(ModelCall.input_tokens).label("input_tokens"),
        func.sum(ModelCall.cached_input_tokens).label("cached_input_tokens"),
        func.sum(ModelCall.cache_write_tokens).label("cache_write_tokens"),
        func.sum(ModelCall.output_tokens).label("output_tokens"),
        func.sum(ModelCall.reasoning_tokens).label("reasoning_tokens"),
        func.sum(case((metered, ModelCall.cost_usd), else_=0)).label("spent_usd"),
        func.sum(case((metered, 0), else_=ModelCall.cost_usd)).label("equivalent_usd"),
        func.count()
        .filter(metered, ModelCall.status == "ok", ModelCall.cost_usd.is_(None))
        .label("unknown_cost_calls"),
    )


async def usage_summary(
    db: Database,
    workspace_id: UUID,
    *,
    since: datetime,
    until: datetime | None = None,
    group_by: str = "day",
    monthly_budget_usd: Decimal | None = None,
    now: datetime | None = None,
) -> UsageSummary:
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {sorted(GROUPS)}")
    now = now or datetime.now(UTC)
    until = until or now
    key = GROUPS[group_by].label("key")
    window = (ModelCall.created_at >= since, ModelCall.created_at <= until)
    async with db.workspace_session(workspace_id) as session:
        grouped = (
            await session.execute(
                select(key, *_measures()).where(*window).group_by(key).order_by(key)
            )
        ).all()
        total = (await session.execute(select(*_measures()).where(*window))).one()
    rows = [
        _row(r.key.date().isoformat() if isinstance(r.key, datetime) else str(r.key), r)
        for r in grouped
    ]
    summary_total = _row("total", total)
    notes = []
    if summary_total.unknown_cost_calls:
        notes.append("some metered calls have no price; add them to a pricing override file")
    return UsageSummary(
        since=since,
        until=until,
        group_by=group_by,
        rows=rows,
        total=summary_total,
        month_spent_usd=await month_spend(db, workspace_id, now),
        monthly_budget_usd=monthly_budget_usd,
        notes=notes,
    )
