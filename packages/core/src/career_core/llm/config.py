"""모델 엔드포인트·라우트·예산 설정 (`models.toml`).

키 값은 설정 파일에 넣지 않는다. `api_key_env`에 환경변수 이름만 적는다.
설정 예시: `docs/architecture/models.example.toml`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

BILLING_METERED = "metered"  # 토큰당 과금 (공식 API)
BILLING_SUBSCRIPTION = "subscription"  # 구독 계정을 프록시로 쓰는 경우 (CLIProxyAPI 등)
BILLING_FREE = "free"  # 로컬 모델 (Ollama 등)
_BILLINGS = {BILLING_METERED, BILLING_SUBSCRIPTION, BILLING_FREE}


@dataclass(frozen=True, slots=True)
class Preset:
    kind: str  # "anthropic" | "openai" (OpenAI 호환 Chat Completions)
    base_url: str | None
    api_key_env: str | None
    pricing_provider: str | None
    billing: str = BILLING_METERED
    token_param: str = "max_tokens"


PRESETS: dict[str, Preset] = {
    "anthropic": Preset("anthropic", None, "ANTHROPIC_API_KEY", "anthropic"),
    "openai": Preset(
        "openai",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "openai",
        token_param="max_completion_tokens",
    ),
    "xai": Preset("openai", "https://api.x.ai/v1", "XAI_API_KEY", "xai"),
    "gemini": Preset(
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini",
    ),
    # CLIProxyAPI: 구독 계정(Claude Code / Codex / Gemini CLI)을 OpenAI 호환 API로 노출하는 로컬 프록시.
    "cliproxyapi": Preset(
        "openai",
        "http://127.0.0.1:8317/v1",
        "CLIPROXY_API_KEY",
        None,
        billing=BILLING_SUBSCRIPTION,
    ),
    "openrouter": Preset("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None),
    "ollama": Preset("openai", "http://127.0.0.1:11434/v1", None, None, billing=BILLING_FREE),
    "openai-compatible": Preset("openai", None, None, None),
}


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    name: str
    kind: str
    base_url: str | None
    api_key_env: str | None
    pricing_provider: str | None
    billing: str
    token_param: str
    # 이 엔드포인트로 보내도 되는 자료 등급. restricted는 적어도 무시된다.
    data_classes: frozenset[str] = frozenset()
    # 구독/무료 엔드포인트에서 "API였다면 얼마"를 보여줄 때 쓸 가격 제공자.
    price_as: str | None = None
    timeout_seconds: float = 120.0
    # Anthropic: 정책상 거절되면 서버가 다른 모델로 다시 돌린다 (server-side fallbacks).
    refusal_fallback: bool = True


@dataclass(frozen=True, slots=True)
class Route:
    endpoint: str
    model: str


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    monthly_usd: Decimal | None = None
    # 종량제 엔드포인트인데 단가를 모르는 모델이면 호출하지 않는다.
    require_known_price: bool = True


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    endpoints: dict[str, EndpointConfig] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    price_files: tuple[Path, ...] = ()

    def route_for(self, purpose: str) -> Route | None:
        return self.routes.get(purpose) or self.routes.get("default")

    def egress_map(self) -> dict[str, frozenset[str]]:
        return {name: ep.data_classes for name, ep in self.endpoints.items()}


class ConfigError(ValueError):
    pass


def parse_route(value: str) -> Route:
    endpoint, sep, model = value.partition("/")
    if not sep or not endpoint or not model:
        raise ConfigError(f"route must be '<endpoint>/<model>': {value!r}")
    return Route(endpoint, model)


def parse_models_config(text: str, *, base_dir: Path | None = None) -> ModelsConfig:
    data = tomllib.loads(text)
    endpoints: dict[str, EndpointConfig] = {}
    for name, raw in data.get("endpoints", {}).items():
        if "/" in name:
            raise ConfigError(f"endpoint name must not contain '/': {name!r}")
        preset_name = raw.get("preset", "openai-compatible")
        preset = PRESETS.get(preset_name)
        if preset is None:
            raise ConfigError(f"unknown preset {preset_name!r} (known: {', '.join(PRESETS)})")
        if "api_key" in raw:
            raise ConfigError("put the key in an environment variable and set api_key_env")
        base_url = raw.get("base_url", preset.base_url)
        if preset.kind == "openai" and not base_url:
            raise ConfigError(f"endpoint {name!r} needs base_url")
        billing = raw.get("billing", preset.billing)
        if billing not in _BILLINGS:
            raise ConfigError(f"billing must be one of {sorted(_BILLINGS)}")
        endpoints[name] = EndpointConfig(
            name=name,
            kind=preset.kind,
            base_url=base_url,
            api_key_env=raw.get("api_key_env", preset.api_key_env),
            pricing_provider=raw.get("pricing_provider", preset.pricing_provider),
            billing=billing,
            token_param=raw.get("token_param", preset.token_param),
            data_classes=frozenset(raw.get("data_classes", [])),
            price_as=raw.get("price_as"),
            timeout_seconds=float(raw.get("timeout_seconds", 120)),
            refusal_fallback=bool(raw.get("refusal_fallback", True)),
        )
    routes = {purpose: parse_route(v) for purpose, v in data.get("routes", {}).items()}
    for purpose, route in routes.items():
        if route.endpoint not in endpoints:
            raise ConfigError(f"route {purpose!r} points to unknown endpoint {route.endpoint!r}")
    raw_budget = data.get("budget", {})
    monthly = raw_budget.get("monthly_usd")
    if isinstance(monthly, float):
        raise ConfigError("budget.monthly_usd must be a string or integer")
    budget = BudgetConfig(
        monthly_usd=Decimal(str(monthly)) if monthly is not None else None,
        require_known_price=bool(raw_budget.get("require_known_price", True)),
    )
    base = base_dir or Path.cwd()
    price_files = tuple(
        (base / p) if not Path(p).is_absolute() else Path(p)
        for p in data.get("pricing", {}).get("files", [])
    )
    return ModelsConfig(endpoints, routes, budget, price_files)


def load_models_config(path: Path | str | None) -> ModelsConfig:
    """파일이 없으면 빈 설정 = 모든 모델 호출 거부."""
    if path is None:
        return ModelsConfig()
    p = Path(path)
    if not p.exists():
        return ModelsConfig()
    return parse_models_config(p.read_text("utf-8"), base_dir=p.parent)
