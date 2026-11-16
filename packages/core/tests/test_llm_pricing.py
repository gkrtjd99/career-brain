"""단가표·비용 계산·설정 파싱 (DB 불필요)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from career_core.adapters.llm_anthropic import usage_parts
from career_core.adapters.llm_openai import reported_cost, usage_from_openai
from career_core.llm import TokenUsage, UsagePart
from career_core.llm.config import ConfigError, parse_models_config
from career_core.llm.pricing import PriceBook, parse_catalog
from career_core.llm.pricing_check import check_prices, compare_xai

TODAY = date(2026, 11, 16)


@pytest.fixture(scope="module")
def book():
    return PriceBook.load()


def part(model, **usage):
    return [UsagePart(model, TokenUsage(**usage))]


def test_catalog_entries_have_source_and_date(book):
    prices = list(book)
    assert {p.provider for p in prices} >= {"anthropic", "openai", "gemini", "xai"}
    for p in prices:
        assert p.source.startswith("https://"), p.model
        assert p.as_of is not None, p.model


def test_anthropic_cost_counts_every_token_kind(book):
    cost = book.cost(
        "anthropic",
        part(
            "claude-opus-5-5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            cache_write_tokens=1_000_000,
            cache_write_long_tokens=1_000_000,
            output_tokens=1_000_000,
        ),
        on=TODAY,
    )
    # 4 + 0.20 + 5 + 8 + 20
    assert cost.usd == Decimal("37.20")
    assert cost.notes == ()
    assert cost.refs == ("anthropic/claude-opus-5-5@2026-11-16",)


def test_long_prompt_tier_applies_to_whole_request(book):
    short = book.cost("anthropic", part("claude-haiku-5-5", input_tokens=100_000), on=TODAY)
    long = book.cost(
        "anthropic",
        part("claude-haiku-5-5", input_tokens=99_000, cached_input_tokens=1_001),
        on=TODAY,
    )
    assert short.usd == Decimal("0.01")  # 정확히 100,000은 기본 단가
    assert (
        long.usd
        == Decimal(99000) * Decimal("0.50") / 10**6 + Decimal(1001) * Decimal("0.05") / 10**6
    )


def test_xai_tier_starts_at_threshold(book):
    at = book.cost("xai", part("grok-4.7", input_tokens=200_000), on=TODAY)
    assert at.usd == Decimal("0.8")  # 200K 이상이면 4.00


def test_scheduled_price_change(book):
    before = book.cost(
        "gemini", part("gemini-4-carbon", output_tokens=10**6), on=date(2026, 11, 30)
    )
    after = book.cost("gemini", part("gemini-4-carbon", output_tokens=10**6), on=date(2026, 12, 1))
    assert (before.usd, after.usd) == (Decimal("10.00"), Decimal("20.00"))
    assert after.refs == ("gemini/gemini-4-carbon@2026-12-01",)


def test_lookup_strips_date_suffix_prefix_and_aliases(book):
    assert book.lookup("gemini", "models/gemini-3.8-flash").model == "gemini-3.8-flash"
    assert book.lookup("openai", "gpt-6.1-sol-2026-09-01").model == "gpt-6.1-sol"
    assert book.lookup("xai", "grok-4.6").model == "grok-4.7"
    assert book.lookup("openai", "claude-opus-5-5") is None


def test_unknown_model_cost_is_none_not_zero(book):
    cost = book.cost("openai", part("gpt-unknown", input_tokens=10), on=TODAY)
    assert cost.usd is None
    assert cost.notes == ("no_price:openai/gpt-unknown",)


def test_missing_cache_price_is_flagged(book):
    # Gemini 4 계열은 캐시 쓰기 단가가 없다 → 입력 단가로 계산하고 표시
    cost = book.cost("gemini", part("gemini-4-hydrogen", cache_write_tokens=10**6), on=TODAY)
    assert cost.usd == Decimal("1.50")
    assert cost.notes == ("cache_write_priced_as_input",)


def test_override_file_wins(tmp_path):
    override = tmp_path / "prices.toml"
    override.write_text(
        '[[models]]\nprovider = "anthropic"\nmodel = "claude-opus-5-5"\n'
        'input = "1"\noutput = "2"\nsource = "https://example.invalid"\nas_of = 2026-11-01\n'
    )
    book = PriceBook.load(override)
    assert book.lookup("anthropic", "claude-opus-5-5").rates.input == Decimal(1)


def test_float_prices_rejected():
    with pytest.raises(TypeError):
        parse_catalog('[[models]]\nprovider="x"\nmodel="m"\ninput=0.1\noutput="1"\n')


def test_openai_usage_reasoning_inside_completion():
    raw = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=100),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=300),
    )
    u = usage_from_openai(raw)
    assert (u.input_tokens, u.cached_input_tokens, u.cache_write_tokens) == (700, 200, 100)
    assert (u.output_tokens, u.reasoning_tokens) == (500, 300)


def test_xai_usage_reasoning_outside_completion_and_ticks():
    # 공식 예시: prompt 32, completion 9, reasoning 94, total 135
    raw = SimpleNamespace(
        prompt_tokens=32,
        completion_tokens=9,
        total_tokens=135,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=None),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=94),
        cost_in_usd_ticks=12_345_000,
    )
    u = usage_from_openai(raw)
    assert (u.output_tokens, u.reasoning_tokens) == (103, 94)
    assert reported_cost(raw) == Decimal("0.0012345")


def test_gemini_compat_missing_reasoning_detail_uses_total():
    raw = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=40,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    u = usage_from_openai(raw)
    assert (u.output_tokens, u.reasoning_tokens) == (30, 25)


def test_anthropic_iterations_are_priced_per_model(book):
    def usage(i, o):
        return SimpleNamespace(
            input_tokens=i,
            output_tokens=o,
            cache_read_input_tokens=0,
            cache_creation=SimpleNamespace(
                ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=0
            ),
        )

    resp = SimpleNamespace(
        model="claude-opus-5",
        usage=SimpleNamespace(
            iterations=[
                SimpleNamespace(type="message", model="claude-opus-5-5", **vars(usage(1000, 10))),
                SimpleNamespace(
                    type="fallback_message", model="claude-opus-5", **vars(usage(1000, 100))
                ),
            ]
        ),
    )
    parts = usage_parts(resp)
    assert [p.model for p in parts] == ["claude-opus-5-5", "claude-opus-5"]
    cost = book.cost("anthropic", parts, on=TODAY)
    expected = (Decimal(1000 * 4 + 10 * 20) + Decimal(1000 * 5 + 100 * 25)) / 10**6
    assert cost.usd == expected


CONFIG = """
[endpoints.claude]
preset = "anthropic"
data_classes = ["public", "private", "restricted"]

[endpoints.proxy]
preset = "cliproxyapi"
price_as = "anthropic"

[endpoints.other]
preset = "openai-compatible"
base_url = "https://example.invalid/v1"
pricing_provider = "nobody"

[routes]
default = "claude/claude-sonnet-5-5"
cheap = "proxy/claude-haiku-5-5"
broken = "other/some-model"
soon = "claude/claude-opus-5-5"

[budget]
monthly_usd = "5"
"""


def test_config_presets_and_routes():
    cfg = parse_models_config(CONFIG)
    assert cfg.endpoints["claude"].api_key_env == "ANTHROPIC_API_KEY"
    assert cfg.endpoints["proxy"].base_url == "http://127.0.0.1:8317/v1"
    assert cfg.endpoints["proxy"].billing == "subscription"
    assert cfg.route_for("unknown-purpose").model == "claude-sonnet-5-5"
    assert cfg.budget.monthly_usd == Decimal(5)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[endpoints.a]\npreset="openai"\napi_key="sk-x"\n', "environment variable"),
        ('[endpoints.a]\npreset="nope"\n', "unknown preset"),
        ('[endpoints.a]\npreset="openai-compatible"\n', "needs base_url"),
        ('[endpoints.a]\npreset="openai"\n[routes]\nx="b/m"\n', "unknown endpoint"),
        ('[endpoints.a]\npreset="openai"\n[routes]\nx="no-slash"\n', "<endpoint>/<model>"),
        ("[budget]\nmonthly_usd = 1.5\n", "string or integer"),
    ],
)
def test_config_errors(text, message):
    with pytest.raises(ConfigError, match=message):
        parse_models_config(text)


def test_pricing_check_findings(book):
    cfg = parse_models_config(CONFIG)
    findings = {(f.code, f.subject.split()[1]) for f in check_prices(book, cfg, today=TODAY)}
    assert ("price_missing", "broken") in findings
    assert not any(code == "price_missing" for code, p in findings if p in {"default", "cheap"})
    stale = check_prices(book, cfg, today=date(2027, 6, 1), max_age_days=60)
    assert any(f.code == "price_stale" for f in stale)
    soon = check_prices(
        book,
        parse_models_config(
            '[endpoints.g]\npreset="gemini"\n[routes]\ndefault="g/gemini-4-carbon"\n'
        ),
        today=TODAY,
    )
    assert any(f.code == "price_change_soon" for f in soon)


def test_compare_xai(book):
    payload = {
        "models": [
            # cents / 100M tokens: 20000 = $2.00 / 1M
            {
                "id": "grok-4.7",
                "prompt_text_token_price": 20000,
                "cached_prompt_text_token_price": 5000,
                "completion_text_token_price": 60000,
            },
            {
                "id": "grok-4.5",
                "prompt_text_token_price": 25000,
                "cached_prompt_text_token_price": 3000,
                "completion_text_token_price": 60000,
            },
            {"id": "grok-9", "prompt_text_token_price": 1, "completion_text_token_price": 2},
        ]
    }
    codes = [(f.code, f.subject) for f in compare_xai(payload, book)]
    assert ("xai_price_differs", "xai/grok-4.5") in codes
    assert ("xai_not_in_catalog", "xai/grok-9") in codes
    assert not any(s == "xai/grok-4.7" for _, s in codes)
