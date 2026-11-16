"""Anthropic Messages API 어댑터 (공식 `anthropic` SDK)."""

from __future__ import annotations

from typing import Any

from career_core.llm.config import EndpointConfig
from career_core.llm.types import (
    ModelCallError,
    ModelCompletion,
    ModelRequest,
    TokenUsage,
    UsagePart,
)

# 서버 측 거절 폴백(`fallbacks: "default"`)을 받는 모델. 그 밖의 모델에 보내면 400이다.
FALLBACK_MODELS = frozenset(
    {
        "claude-fable-5-2",
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-sonnet-5-5",
    }
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _usage(raw: Any) -> TokenUsage:
    cache = getattr(raw, "cache_creation", None)
    write_5m = getattr(cache, "ephemeral_5m_input_tokens", None) if cache else None
    write_1h = getattr(cache, "ephemeral_1h_input_tokens", None) if cache else None
    if write_5m is None and write_1h is None:
        write_5m, write_1h = getattr(raw, "cache_creation_input_tokens", 0) or 0, 0
    details = getattr(raw, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        cached_input_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=write_5m or 0,
        cache_write_long_tokens=write_1h or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        reasoning_tokens=(getattr(details, "thinking_tokens", 0) or 0) if details else 0,
    )


def usage_parts(response: Any) -> tuple[UsagePart, ...]:
    """폴백이 돌았으면 `usage.iterations`가 시도별 과금 근거다. 없으면 최상위 usage 하나."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return ()
    iterations = getattr(usage, "iterations", None)
    if iterations:
        return tuple(
            UsagePart(getattr(it, "model", None) or response.model, _usage(it)) for it in iterations
        )
    return (UsagePart(response.model, _usage(usage)),)


class AnthropicModelClient:
    def __init__(
        self, endpoint: EndpointConfig, api_key: str | None, *, http_client: Any = None
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - 설치 구성 문제
            raise ModelCallError("sdk_not_installed") from exc
        self._sdk = anthropic
        self._endpoint = endpoint
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": endpoint.timeout_seconds,
            "max_retries": 2,
        }
        if endpoint.base_url:
            kwargs["base_url"] = endpoint.base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = anthropic.AsyncAnthropic(**kwargs)

    async def complete(self, model: str, request: ModelRequest) -> ModelCompletion:
        sdk = self._sdk
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.system:
            params["system"] = request.system
        try:
            if self._endpoint.refusal_fallback and model in FALLBACK_MODELS:
                resp = await self._client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                resp = await self._client.messages.create(**params)
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

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return ModelCompletion(
            text=text,
            model=resp.model,
            stop_reason=resp.stop_reason,
            parts=usage_parts(resp),
            request_id=getattr(resp, "_request_id", None),
        )
