"""단가표 점검: 오래된 항목, 라우트에 쓰였는데 단가가 없는 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from career_core.llm.config import BILLING_METERED, ModelsConfig
from career_core.llm.pricing import PriceBook

UPCOMING_DAYS = 45


@dataclass(frozen=True, slots=True)
class Finding:
    level: str  # "error" | "warn" | "info"
    code: str
    subject: str
    detail: str = ""


def check_prices(
    prices: PriceBook, config: ModelsConfig, *, today: date, max_age_days: int = 60
) -> list[Finding]:
    out: list[Finding] = []
    for purpose, route in sorted(config.routes.items()):
        ep = config.endpoints[route.endpoint]
        provider = ep.pricing_provider if ep.billing == BILLING_METERED else ep.price_as
        subject = f"route {purpose} -> {route.endpoint}/{route.model}"
        if provider is None:
            if ep.billing == BILLING_METERED:
                out.append(Finding("error", "no_pricing_provider", subject, "set pricing_provider"))
            continue
        price = prices.lookup(provider, route.model)
        if price is None:
            level = "error" if ep.billing == BILLING_METERED else "warn"
            out.append(
                Finding(level, "price_missing", subject, f"no {provider} price; add an override")
            )
            continue
        if price.as_of is None or (today - price.as_of).days > max_age_days:
            age = "unknown" if price.as_of is None else f"{(today - price.as_of).days}d"
            out.append(Finding("warn", "price_stale", subject, f"as_of {age}: {price.source}"))
        for change in price.upcoming(today):
            if (change.effective_from - today).days <= UPCOMING_DAYS:
                out.append(
                    Finding(
                        "warn",
                        "price_change_soon",
                        subject,
                        f"from {change.effective_from}: in {change.rates.input} / "
                        f"out {change.rates.output} USD/1M",
                    )
                )
    for price in prices:
        if price.as_of is None or (today - price.as_of).days > max_age_days:
            out.append(
                Finding("info", "catalog_stale", price.ref(), price.source or "no source recorded")
            )
    return out


XAI_LANGUAGE_MODELS_URL = "https://api.x.ai/v1/language-models"
_XAI_UNIT = Decimal(10_000)  # 응답 단위: USD cents / 100M tokens → USD / 1M tokens


def _xai_price(raw: dict, key: str) -> Decimal | None:
    value = raw.get(key)
    if value in (None, 0):
        return None  # 0은 "기본 단가를 따름"
    return Decimal(int(value)) / _XAI_UNIT


def compare_xai(payload: dict, prices: PriceBook) -> list[Finding]:
    """xAI 공식 단가 API 응답과 단가표를 비교한다."""
    out: list[Finding] = []
    for raw in payload.get("models", []):
        model_id = raw.get("id", "")
        local = prices.lookup("xai", model_id)
        remote = {
            "input": _xai_price(raw, "prompt_text_token_price"),
            "cached_input": _xai_price(raw, "cached_prompt_text_token_price"),
            "output": _xai_price(raw, "completion_text_token_price"),
        }
        if local is None:
            shown = ", ".join(f"{k} {v}" for k, v in remote.items() if v is not None)
            out.append(Finding("warn", "xai_not_in_catalog", f"xai/{model_id}", shown))
            continue
        for name, value in remote.items():
            mine = getattr(local.rates, name)
            if value is not None and mine != value:
                out.append(
                    Finding(
                        "error",
                        "xai_price_differs",
                        f"xai/{model_id}",
                        f"{name}: catalog {mine} vs api {value} USD/1M",
                    )
                )
    return out
