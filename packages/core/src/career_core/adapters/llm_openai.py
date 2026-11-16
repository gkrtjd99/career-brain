"""OpenAI 호환 Chat Completions 어댑터 (공식 `openai` SDK).

OpenAI, xAI(Grok), Gemini의 OpenAI 호환 엔드포인트, CLIProxyAPI, OpenRouter, Ollama가 이 하나로 붙는다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from career_core.llm.config import EndpointConfig
from career_core.llm.types import (
    ModelCallError,
    ModelCompletion,
    ModelRequest,
    TokenUsage,
    UsagePart,
)

_NO_KEY = "not-needed"  # 키가 없는 로컬 엔드포인트용. SDK가 빈 키를 거부한다.


USD_TICKS = Decimal(10_000_000_000)  # xAI: 1 USD = 1e10 ticks


def _extra(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if value is None:
        value = (getattr(obj, "model_extra", None) or {}).get(name)
    return value


def usage_from_openai(raw: Any) -> TokenUsage:
    """OpenAI 호환 usage를 정규화한다.

    제공자마다 reasoning을 세는 방식이 다르다. OpenAI는 completion_tokens 안에 넣고,
    xAI는 밖에 따로 둔다 (total = prompt + completion + reasoning). Gemini 호환 계층은
    reasoning 내역을 빼먹기도 한다. 그래서 출력 = max(completion, total - prompt)로 잡는다.
    """
    prompt = getattr(raw, "prompt_tokens", 0) or 0
    completion = getattr(raw, "completion_tokens", 0) or 0
    total = getattr(raw, "total_tokens", 0) or 0
    pdetails = getattr(raw, "prompt_tokens_details", None)
    cdetails = getattr(raw, "completion_tokens_details", None)
    cached = (getattr(pdetails, "cached_tokens", 0) or 0) if pdetails else 0
    written = (getattr(pdetails, "cache_write_tokens", 0) or 0) if pdetails else 0
    reasoning = (getattr(cdetails, "reasoning_tokens", 0) or 0) if cdetails else 0
    output = max(completion, total - prompt)
    if not reasoning and output > completion:
        reasoning = output - completion
    return TokenUsage(
        # prompt_tokens는 캐시 적중·쓰기분을 포함한다. 기본 단가 몫만 남긴다.
        input_tokens=max(prompt - cached - written, 0),
        cached_input_tokens=cached,
        cache_write_tokens=written,
        output_tokens=output,
        reasoning_tokens=reasoning,
    )


def reported_cost(raw: Any) -> Decimal | None:
    ticks = _extra(raw, "cost_in_usd_ticks")
    if ticks is None:
        return None
    try:
        return Decimal(int(ticks)) / USD_TICKS
    except (TypeError, ValueError):
        return None


class OpenAICompatibleClient:
    def __init__(
        self, endpoint: EndpointConfig, api_key: str | None, *, http_client: Any = None
    ) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - 설치 구성 문제
            raise ModelCallError("sdk_not_installed") from exc
        self._sdk = openai
        self._token_param = endpoint.token_param
        kwargs: dict[str, Any] = {
            "api_key": api_key or _NO_KEY,
            "base_url": endpoint.base_url,
            "timeout": endpoint.timeout_seconds,
            "max_retries": 2,
        }
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = openai.AsyncOpenAI(**kwargs)

    async def complete(self, model: str, request: ModelRequest) -> ModelCompletion:
        sdk = self._sdk
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                **{self._token_param: request.max_output_tokens},
            )
        except sdk.APITimeoutError as exc:
            raise ModelCallError("timeout", retryable=True) from exc
        except sdk.RateLimitError as exc:
            raise ModelCallError("rate_limited", retryable=True) from exc
        except sdk.AuthenticationError as exc:
            raise ModelCallError("auth_failed") from exc
        except sdk.PermissionDeniedError as exc:
            raise ModelCallError("permission_denied") from exc
        except sdk.NotFoundError as exc:
            raise ModelCallError("model_not_found") from exc
        except sdk.BadRequestError as exc:
            raise ModelCallError("bad_request") from exc
        except sdk.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise ModelCallError(f"http_{exc.status_code}", retryable=retryable) from exc
        except sdk.APIConnectionError as exc:
            raise ModelCallError("connection_error", retryable=True) from exc

        choice = resp.choices[0] if resp.choices else None
        served = resp.model or model
        parts = (UsagePart(served, usage_from_openai(resp.usage)),) if resp.usage else ()
        return ModelCompletion(
            text=(choice.message.content or "") if choice else "",
            model=served,
            stop_reason=choice.finish_reason if choice else None,
            parts=parts,
            request_id=getattr(resp, "_request_id", None) or resp.id,
            reported_cost_usd=reported_cost(resp.usage) if resp.usage else None,
        )
