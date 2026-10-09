"""The provider adapters, run through the real SDKs against a mocked HTTP transport."""

import json

import anthropic
import httpx2
import openai
import pytest

from app.config import settings
from app.services import llm

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def call(provider):
    return provider.complete_json(
        system="Be brief.", prompt="Say hi.", schema=SCHEMA, schema_name="greeting", max_tokens=500
    )


def mocked(provider, sdk, handler):
    """Point `provider` at `handler` instead of the network, with no retries."""
    requests = []

    def record(request):
        requests.append(json.loads(request.content))
        return handler(request)

    provider.client = sdk(
        api_key="test-key", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(record))
    )
    return requests


def anthropic_message(text, stop_reason="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 40, "output_tokens": 7},
    }


def openai_completion(content, finish_reason="stop", refusal=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.4-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 7, "total_tokens": 47},
    }


def test_anthropic_requests_schema_json_at_low_effort():
    provider = llm.AnthropicProvider("test-key", "claude-haiku-5-5")
    sent = mocked(provider, anthropic.Anthropic, lambda r: httpx2.Response(200, json=anthropic_message('{"answer": "hi"}')))

    result = call(provider)

    assert result.data == {"answer": "hi"}
    assert (result.input_tokens, result.output_tokens) == (40, 7)
    body = sent[0]
    assert body["model"] == "claude-haiku-5-5"
    assert body["system"] == "Be brief."
    assert body["messages"] == [{"role": "user", "content": "Say hi."}]
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx2.Response(200, json=anthropic_message("", "refusal")), "declined"),
        (httpx2.Response(200, json=anthropic_message('{"ans', "max_tokens")), "cut off"),
        (httpx2.Response(200, json=anthropic_message("not json")), "malformed JSON"),
        (httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad"}}), "ANTHROPIC_API_KEY"),
        (httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "model"}}), "ANTHROPIC_MODEL"),
        (httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}), "rate-limiting"),
        (httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}), "HTTP 500"),
    ],
)
def test_anthropic_failures_become_readable_errors(response, message):
    provider = llm.AnthropicProvider("test-key", "claude-haiku-5-5")
    mocked(provider, anthropic.Anthropic, lambda r: response)
    with pytest.raises(llm.LLMError, match=message):
        call(provider)


def test_anthropic_connection_failure():
    provider = llm.AnthropicProvider("test-key", "claude-haiku-5-5")

    def fail(request):
        raise httpx2.ConnectError("offline")

    mocked(provider, anthropic.Anthropic, fail)
    with pytest.raises(llm.LLMError, match="Couldn't reach"):
        call(provider)


def test_openai_requests_strict_json_schema():
    provider = llm.OpenAIProvider("test-key", "gpt-5.4-mini")
    sent = mocked(provider, openai.OpenAI, lambda r: httpx2.Response(200, json=openai_completion('{"answer": "hi"}')))

    result = call(provider)

    assert result.data == {"answer": "hi"}
    assert (result.input_tokens, result.output_tokens) == (40, 7)
    body = sent[0]
    assert body["model"] == "gpt-5.4-mini"
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Say hi."},
    ]
    assert body["max_completion_tokens"] == 500
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "greeting", "schema": SCHEMA, "strict": True},
    }
    assert body["reasoning_effort"] == "low"


def test_openai_skips_reasoning_effort_for_non_reasoning_models():
    provider = llm.OpenAIProvider("test-key", "gpt-4.1-mini")
    sent = mocked(provider, openai.OpenAI, lambda r: httpx2.Response(200, json=openai_completion('{"answer": "hi"}')))
    call(provider)
    assert "reasoning_effort" not in sent[0]


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx2.Response(200, json=openai_completion(None, refusal="No.")), "declined"),
        (httpx2.Response(200, json=openai_completion('{"ans', "length")), "cut off"),
        (httpx2.Response(401, json={"error": {"message": "bad key", "type": "invalid_request_error"}}), "OPENAI_API_KEY"),
        (httpx2.Response(429, json={"error": {"message": "quota", "type": "insufficient_quota"}}), "rate-limiting"),
    ],
)
def test_openai_failures_become_readable_errors(response, message):
    provider = llm.OpenAIProvider("test-key", "gpt-5.4-mini")
    mocked(provider, openai.OpenAI, lambda r: response)
    with pytest.raises(llm.LLMError, match=message):
        call(provider)


@pytest.fixture()
def keys(monkeypatch):
    def set_keys(anthropic_key=None, openai_key=None, default="anthropic", **models):
        monkeypatch.setattr(settings, "anthropic_api_key", anthropic_key)
        monkeypatch.setattr(settings, "openai_api_key", openai_key)
        monkeypatch.setattr(settings, "llm_provider", default)
        monkeypatch.setattr(settings, "anthropic_model", models.get("anthropic_model"))
        monkeypatch.setattr(settings, "openai_model", models.get("openai_model"))

    return set_keys


def test_settings_lists_no_providers_without_keys(client):
    assert client.get("/api/settings/llm").json() == {"default": None, "providers": []}


def test_settings_lists_only_providers_with_keys(client, keys):
    keys(anthropic_key="sk-ant-test")
    assert client.get("/api/settings/llm").json() == {
        "default": "anthropic",
        "providers": [{"name": "anthropic", "label": "Anthropic Claude", "model": "claude-haiku-5-5"}],
    }

    keys(anthropic_key="sk-ant-test", openai_key="sk-test", default="openai", openai_model="gpt-4.1-mini")
    data = client.get("/api/settings/llm").json()
    assert data["default"] == "openai"
    assert [(p["name"], p["model"]) for p in data["providers"]] == [
        ("anthropic", "claude-haiku-5-5"),
        ("openai", "gpt-4.1-mini"),
    ]


def test_default_falls_back_to_a_provider_with_a_key(client, keys):
    keys(openai_key="sk-test", default="anthropic")
    assert client.get("/api/settings/llm").json()["default"] == "openai"
    assert llm.resolve().name == "openai"


def test_resolve_explains_what_is_missing(keys):
    keys()
    with pytest.raises(llm.LLMNotConfigured, match="No LLM is configured"):
        llm.resolve()
    keys(anthropic_key="sk-ant-test")
    with pytest.raises(llm.LLMNotConfigured, match="OpenAI has no API key"):
        llm.resolve("openai")
    with pytest.raises(llm.LLMNotConfigured, match="Unknown LLM provider"):
        llm.resolve("gemini")
    assert llm.resolve("anthropic").name == "anthropic"
