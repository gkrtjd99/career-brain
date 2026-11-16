"""모델 단가표와 비용 계산.

단가는 제공자 공식 가격 페이지에서 옮긴 값이다 (`catalog.toml`, 항목마다 출처와 확인 날짜).
가격을 모르면 비용은 0이 아니라 None이다. 조용히 0으로 세면 예산이 무력해진다.

- 캐시 쓰기는 짧은 TTL(`cache_write`: Anthropic 5분, OpenAI 30분)과
  긴 TTL(`cache_write_long`: Anthropic 1시간, OpenAI 12시간) 두 칸이다.
- 장문 구간(`tiers`)은 프롬프트(입력+캐시 읽기/쓰기) 토큰이 `min_prompt_tokens` 이상이면 요청 전체에 적용한다.
- 예고된 가격 변경(`changes`)은 `effective_from` 날짜부터 기본 단가를 대체한다.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from importlib import resources
from pathlib import Path

from career_core.llm.types import TokenUsage, UsagePart

MILLION = Decimal(1_000_000)
_DATE_SUFFIX = re.compile(r"(-\d{4}-\d{2}-\d{2}|-\d{8}|@\d{8})$")


@dataclass(frozen=True, slots=True)
class Rates:
    """USD / 1M tokens. None이면 그 항목 단가를 옮기지 않았다 (입력 단가로 계산하고 표시)."""

    input: Decimal
    output: Decimal
    cached_input: Decimal | None = None
    cache_write: Decimal | None = None
    cache_write_long: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Tier:
    min_prompt_tokens: int
    rates: Rates


@dataclass(frozen=True, slots=True)
class PriceChange:
    effective_from: date
    rates: Rates
    tiers: tuple[Tier, ...] | None = None  # None이면 기존 구간 유지


@dataclass(frozen=True, slots=True)
class ModelPrice:
    provider: str
    model: str
    rates: Rates
    tiers: tuple[Tier, ...] = ()
    changes: tuple[PriceChange, ...] = ()
    source: str = ""
    as_of: date | None = None
    aliases: tuple[str, ...] = ()

    def ref(self, on: date | None = None) -> str:
        change = self.change_on(on) if on else None
        stamp = change.effective_from if change else self.as_of
        return f"{self.provider}/{self.model}@{stamp.isoformat() if stamp else '?'}"

    def change_on(self, on: date) -> PriceChange | None:
        applicable = [c for c in self.changes if c.effective_from <= on]
        return max(applicable, key=lambda c: c.effective_from) if applicable else None

    def upcoming(self, after: date) -> list[PriceChange]:
        return sorted(
            (c for c in self.changes if c.effective_from > after), key=lambda c: c.effective_from
        )

    def rates_for(self, prompt_tokens: int, on: date | None = None) -> Rates:
        base, tiers = self.rates, self.tiers
        change = self.change_on(on) if on else None
        if change is not None:
            base = change.rates
            if change.tiers is not None:
                tiers = change.tiers
        chosen = base
        for tier in sorted(tiers, key=lambda t: t.min_prompt_tokens):
            if prompt_tokens >= tier.min_prompt_tokens:
                chosen = tier.rates
        return chosen


@dataclass(frozen=True, slots=True)
class Cost:
    usd: Decimal | None
    refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def known(self) -> bool:
        return self.usd is not None


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("prices must be strings or integers, not floats")
    return Decimal(str(value))


def _rates(raw: dict) -> Rates:
    inp, out = _dec(raw.get("input")), _dec(raw.get("output"))
    if inp is None or out is None:
        raise ValueError("input and output prices are required")
    return Rates(
        input=inp,
        output=out,
        cached_input=_dec(raw.get("cached_input")),
        cache_write=_dec(raw.get("cache_write")),
        cache_write_long=_dec(raw.get("cache_write_long")),
    )


def _tiers(raw: list[dict]) -> tuple[Tier, ...]:
    return tuple(Tier(int(t["min_prompt_tokens"]), _rates(t)) for t in raw)


def normalize_model_id(model: str) -> str:
    return model.strip().removeprefix("models/")  # Gemini가 붙이는 접두어


class PriceBook:
    def __init__(self, prices: list[ModelPrice] | None = None) -> None:
        self._by_key: dict[tuple[str, str], ModelPrice] = {}
        for price in prices or []:
            self.add(price)

    def add(self, price: ModelPrice) -> None:
        self._by_key[(price.provider, price.model)] = price
        for alias in price.aliases:
            self._by_key[(price.provider, alias)] = price

    def __iter__(self):
        seen: set[int] = set()
        for price in self._by_key.values():
            if id(price) not in seen:
                seen.add(id(price))
                yield price

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        model = normalize_model_id(model)
        hit = self._by_key.get((provider, model))
        if hit is None:
            hit = self._by_key.get((provider, _DATE_SUFFIX.sub("", model)))
        return hit

    def cost(
        self, provider: str, parts: tuple[UsagePart, ...] | list[UsagePart], *, on: date
    ) -> Cost:
        total = Decimal(0)
        refs: list[str] = []
        notes: list[str] = []
        for part in parts:
            price = self.lookup(provider, part.model)
            if price is None:
                return Cost(None, tuple(refs), (f"no_price:{provider}/{part.model}",))
            refs.append(price.ref(on))
            usd, part_notes = usage_cost(price.rates_for(part.usage.prompt_tokens, on), part.usage)
            total += usd
            notes.extend(part_notes)
        return Cost(total, tuple(refs), tuple(dict.fromkeys(notes)))

    @classmethod
    def load(cls, *paths: Path | str, include_default: bool = True) -> PriceBook:
        book = cls()
        if include_default:
            text = resources.files("career_core.llm").joinpath("catalog.toml").read_text("utf-8")
            for price in parse_catalog(text):
                book.add(price)
        for path in paths:
            for price in parse_catalog(Path(path).read_text("utf-8")):
                book.add(price)  # 뒤에 읽은 파일이 이긴다 (사용자 덮어쓰기)
        return book


def usage_cost(rates: Rates, usage: TokenUsage) -> tuple[Decimal, list[str]]:
    notes: list[str] = []

    def rate(value: Decimal | None, name: str, tokens: int) -> Decimal:
        if value is None:
            if tokens:
                notes.append(f"{name}_priced_as_input")
            return rates.input
        return value

    usd = (
        usage.input_tokens * rates.input
        + usage.cached_input_tokens
        * rate(rates.cached_input, "cached_input", usage.cached_input_tokens)
        + usage.cache_write_tokens
        * rate(rates.cache_write, "cache_write", usage.cache_write_tokens)
        + usage.cache_write_long_tokens
        * rate(rates.cache_write_long, "cache_write_long", usage.cache_write_long_tokens)
        + usage.output_tokens * rates.output
    ) / MILLION
    return usd, notes


def parse_catalog(text: str) -> list[ModelPrice]:
    data = tomllib.loads(text)
    providers = data.get("providers", {})
    out: list[ModelPrice] = []
    for raw in data.get("models", []):
        provider = raw["provider"]
        defaults = providers.get(provider, {})
        as_of = raw.get("as_of", defaults.get("as_of"))
        out.append(
            ModelPrice(
                provider=provider,
                model=raw["model"],
                rates=_rates(raw),
                tiers=_tiers(raw.get("tiers", [])),
                changes=tuple(
                    PriceChange(
                        effective_from=c["effective_from"],
                        rates=_rates(c),
                        tiers=_tiers(c["tiers"]) if "tiers" in c else None,
                    )
                    for c in raw.get("changes", [])
                ),
                source=raw.get("source", defaults.get("source", "")),
                as_of=as_of if isinstance(as_of, date) else None,
                aliases=tuple(raw.get("aliases", [])),
            )
        )
    return out
