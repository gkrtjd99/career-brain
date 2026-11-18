"""TypeSafe System One 어댑터 (Jev). ``POST {base_url}/systemone``.

글을 만들지 않는다. 판단할 자료(``state``)와 질문(``questions``: noul·score·choice)을 보내면 질문마다 확률·점수를
돌려준다. 과금은 입력 토큰만 (출력 토큰은 무료). 문서: https://docs.typesafe.ai/api
"""

from __future__ import annotations

from typing import Any

import httpx

from career_core.llm.config import EndpointConfig
from career_core.llm.types import (
    JudgmentCompletion,
    ModelCallError,
    ModelCompletion,
    ModelRequest,
    TokenUsage,
    UsagePart,
)

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
_ERRORS = {
    400: "bad_request",
    401: "unauthorized",
    403: "unauthorized",
    413: "too_large",
    422: "invalid_request",
    429: "rate_limited",
    529: "overloaded",
}


class TypeSafeClient:
    def __init__(
        self,
        endpoint: EndpointConfig,
        api_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = (endpoint.base_url or DEFAULT_BASE_URL).rstrip("/") + "/systemone"
        self._key = api_key
        self._timeout = endpoint.timeout_seconds
        self._transport = transport

    async def complete(self, model: str, request: ModelRequest) -> ModelCompletion:
        raise ModelCallError("not_chat_model")  # 판정 모델은 글을 만들지 않는다

    async def judge(
        self, model: str, state: dict, questions: dict[str, dict]
    ) -> JudgmentCompletion:
        headers = {"Content-Type": "application/json", "User-Agent": "career-brain"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        body = {"model": model, "state": state, "questions": questions}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout, follow_redirects=False
            ) as client:
                resp = await client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelCallError("timeout") from exc
        except (httpx.TransportError, httpx.InvalidURL) as exc:
            raise ModelCallError("unreachable") from exc
        if resp.status_code != 200:
            code = _ERRORS.get(resp.status_code, "bad_response")
            if resp.status_code == 400 and "max_tokens" in resp.text:
                code = "too_long"
            raise ModelCallError(code)
        try:
            data: Any = resp.json()
        except ValueError as exc:
            raise ModelCallError("bad_response") from exc
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            raise ModelCallError("bad_response")
        served = str(data.get("model") or model)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens = TokenUsage(
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
        )
        parts = (UsagePart(served, tokens),) if usage else ()
        return JudgmentCompletion(
            answers={k: v for k, v in answers.items() if isinstance(v, dict)},
            model=served,
            parts=parts,
            request_id=resp.headers.get("x-request-id"),
        )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


__all__ = ["DEFAULT_BASE_URL", "TypeSafeClient"]
