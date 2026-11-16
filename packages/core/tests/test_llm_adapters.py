"""제공자 어댑터: 실제 SDK에 가짜 HTTP 전송을 붙여 요청 모양과 응답 정규화를 확인한다."""

import json

import anthropic
import httpx2
import openai
import pytest
from career_core.adapters.llm_anthropic import FALLBACK_BETA, AnthropicModelClient
from career_core.adapters.llm_openai import OpenAICompatibleClient
from career_core.llm import ChatMessage, ModelCallError, ModelRequest
from career_core.llm.config import parse_models_config

pytestmark = pytest.mark.anyio

REQ = ModelRequest(
    messages=(ChatMessage("user", "hi"),),
    system="be brief",
    max_output_tokens=64,
    data_classes=frozenset({"public"}),
)
CFG = parse_models_config(
    '[endpoints.oa]\npreset="openai"\n[endpoints.gm]\npreset="gemini"\n'
    '[endpoints.cl]\npreset="anthropic"\n'
)


def recorder(status, body):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(status, json=body)

    return seen, handler


async def test_openai_request_shape_and_usage():
    seen, handler = recorder(
        200,
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-6.1-sol",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "total_tokens": 23,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
        },
    )
    http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    client = OpenAICompatibleClient(CFG.endpoints["oa"], "sk-test", http_client=http)
    out = await client.complete("gpt-6.1-sol", REQ)
    body = json.loads(seen[0].content)
    assert seen[0].url.host == "api.openai.com"
    assert body["max_completion_tokens"] == 64 and "max_tokens" not in body
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert out.text == "OK" and out.stop_reason == "stop"
    assert (out.usage.input_tokens, out.usage.cached_input_tokens) == (15, 5)


async def test_gemini_preset_uses_compat_base_url_and_max_tokens():
    seen, handler = recorder(
        200,
        {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "gemini-3.8-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    client = OpenAICompatibleClient(CFG.endpoints["gm"], "k", http_client=http)
    await client.complete("gemini-3.8-flash", REQ)
    assert str(seen[0].url).startswith(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert json.loads(seen[0].content)["max_tokens"] == 64


async def test_openai_auth_error_is_mapped():
    _, handler = recorder(401, {"error": {"message": "bad key", "type": "invalid_request_error"}})
    http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    client = OpenAICompatibleClient(CFG.endpoints["oa"], "sk-bad", http_client=http)
    with pytest.raises(ModelCallError) as err:
        await client.complete("gpt-6.1-sol", REQ)
    assert err.value.code == "auth_failed" and not err.value.retryable


def anthropic_body(model):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "OK"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 6,
            "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 5},
        },
    }


@pytest.mark.parametrize(
    ("model", "fallback"), [("claude-opus-5-5", True), ("claude-haiku-5-5", False)]
)
async def test_anthropic_fallback_only_where_supported(model, fallback):
    seen, handler = recorder(200, anthropic_body(model))
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    client = AnthropicModelClient(CFG.endpoints["cl"], "sk-ant-test", http_client=http)
    out = await client.complete(model, REQ)
    body = json.loads(seen[0].content)
    assert body["system"] == "be brief" and body["max_tokens"] == 64
    assert (body.get("fallbacks") == "default") is fallback
    assert (FALLBACK_BETA in seen[0].headers.get("anthropic-beta", "")) is fallback
    u = out.usage
    assert (
        u.input_tokens,
        u.cached_input_tokens,
        u.cache_write_tokens,
        u.cache_write_long_tokens,
    ) == (10, 4, 1, 5)
    assert out.text == "OK"
