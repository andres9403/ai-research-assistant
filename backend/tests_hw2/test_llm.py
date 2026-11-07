"""Tests for services/llm.py: provider selection, Ollama detection and each provider's response handling.

Real SDK clients are created (that makes no request) and then replaced with stubs.
"""

from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import openai
import pytest

from app.config import settings
from app.services import http, llm
from app.services.llm import LLMError, LLMNotConfigured

from tests_hw2.helpers import json_response, mock_client

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
CALL = dict(system="sys", prompt="prompt", schema=SCHEMA, schema_name="thing", max_tokens=5000)


@pytest.fixture
def ollama_server(monkeypatch):
    """A mock Ollama at http://ollama.test:11434; set `state['tags']`/`state['chat']` to shape replies."""
    state = {"tags": json_response({"models": [{"name": "gemma3:4b"}]}), "chat": None, "requests": []}

    def handler(request):
        state["requests"].append(request)
        reply = state["tags"] if request.url.path == "/api/tags" else state["chat"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(settings, "ollama_url", "http://ollama.test:11434/")
    monkeypatch.setattr(http, "make_client", lambda timeout=10.0: mock_client(handler))
    return state


# ----------------------------------------------------------------------- selection


def test_resolve_without_any_provider_explains_setup():
    with pytest.raises(LLMNotConfigured) as info:
        llm.resolve()
    assert "No LLM is set up" in str(info.value)
    assert "Install Ollama" in str(info.value)


def test_available_lists_keyed_providers_with_default_models(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "openai_api_key", "sk-openai-test")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    providers = llm.available()
    assert list(providers) == ["anthropic", "openai"]
    assert providers["anthropic"].model == llm.DEFAULT_MODELS["anthropic"]
    assert providers["openai"].model == "gpt-4o-mini"
    assert llm.available()["anthropic"] is providers["anthropic"]  # built once and cached


def test_resolve_default_and_named_providers(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-openai-test")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")  # no key, so the first available one wins
    assert llm.resolve().name == "openai"
    assert llm.resolve("openai").name == "openai"
    with pytest.raises(LLMNotConfigured, match="Anthropic Claude has no API key"):
        llm.resolve("anthropic")
    with pytest.raises(LLMNotConfigured, match="Unknown LLM provider 'gemini'"):
        llm.resolve("gemini")
    with pytest.raises(LLMNotConfigured, match="Ollama isn't running"):
        llm.resolve("ollama")


def test_default_name_prefers_configured_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    assert llm.default_name({"anthropic": object(), "openai": object()}) == "openai"
    assert llm.default_name({"anthropic": object()}) == "anthropic"
    assert llm.default_name({}) is None


def test_ollama_status_is_off_with_blank_url():
    status = llm.ollama_status()
    assert (status.model, status.running, status.pulled) == ("gemma3:4b", False, False)


def test_ollama_detected_when_model_pulled(ollama_server):
    status = llm.ollama_status()
    assert status.running and status.pulled
    assert ollama_server["requests"][0].url == "http://ollama.test:11434/api/tags"
    provider = llm.resolve("ollama")
    assert (provider.name, provider.model) == ("ollama", "gemma3:4b")


def test_ollama_running_without_model_is_not_available(ollama_server):
    ollama_server["tags"] = json_response({"models": [{"name": "llama3.2:latest"}]})
    status = llm.ollama_status()
    assert status.running and not status.pulled
    assert "ollama" not in llm.available(status)
    with pytest.raises(LLMNotConfigured, match="ollama pull gemma3:4b"):
        llm.resolve("ollama")


def test_ollama_model_without_tag_matches_latest(ollama_server, monkeypatch):
    monkeypatch.setattr(settings, "ollama_model", "llama3.2")
    ollama_server["tags"] = json_response({"models": [{"name": "llama3.2:latest"}]})
    assert llm.ollama_status().pulled is True


@pytest.mark.parametrize(
    "reply", [httpx.ConnectError("refused"), httpx.Response(500), httpx.Response(200, text="nope"),
              json_response({"unexpected": True})]
)
def test_ollama_status_treats_errors_as_not_running(ollama_server, reply):
    ollama_server["tags"] = reply
    assert llm.ollama_status().running is False


def test_setup_hint_depends_on_ollama_state():
    assert "ollama pull" in llm.setup_hint(llm.OllamaStatus("m", running=True, pulled=False))
    assert "https://ollama.com" in llm.setup_hint(llm.OllamaStatus("m", running=False, pulled=False))


# ----------------------------------------------------------------------- JSON parsing


def test_parse_accepts_objects_only():
    assert llm._parse('{"a": 1}') == {"a": 1}
    with pytest.raises(LLMError, match="malformed"):
        llm._parse("not json")
    with pytest.raises(LLMError, match="wrong shape"):
        llm._parse("[1, 2]")


# ----------------------------------------------------------------------- Ollama provider


def test_ollama_provider_sends_schema_and_limits(ollama_server):
    ollama_server["chat"] = json_response(
        {"message": {"content": '{"a": 7}'}, "done_reason": "stop", "prompt_eval_count": 11, "eval_count": 3}
    )
    result = llm.OllamaProvider("http://ollama.test:11434/", "gemma3:4b").complete_json(**CALL)
    assert (result.data, result.input_tokens, result.output_tokens) == ({"a": 7}, 11, 3)
    request = ollama_server["requests"][-1]
    assert request.url == "http://ollama.test:11434/api/chat"
    body = __import__("json").loads(request.content)
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert body["options"] == {"num_ctx": settings.ollama_num_ctx, "num_predict": llm.OLLAMA_MAX_OUTPUT_TOKENS}
    assert body["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    "reply, message",
    [
        (httpx.Response(404), "ollama pull gemma3:4b"),
        (httpx.Response(500, text="boom"), "HTTP 500"),
        (httpx.Response(200, text="<html>"), "isn't JSON"),
        (json_response({"message": {"content": "{}"}, "done_reason": "length"}), "cut off"),
        (json_response({"message": {"content": "garbage"}}), "malformed JSON"),
        (httpx.ReadTimeout("slow"), "OLLAMA_TIMEOUT"),
        (httpx.ConnectError("refused"), "Check that it is running"),
    ],
)
def test_ollama_provider_errors(ollama_server, reply, message):
    ollama_server["chat"] = reply
    with pytest.raises(LLMError, match=message):
        llm.OllamaProvider("http://ollama.test:11434", "gemma3:4b").complete_json(**CALL)


# ----------------------------------------------------------------------- Anthropic provider


def anthropic_provider(create):
    provider = llm.AnthropicProvider("sk-ant-test", "claude-test")
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=create),
                                      models=SimpleNamespace(retrieve=create))
    return provider


def anthropic_reply(text='{"a": 1}', stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", text="ignored"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
    )


def test_anthropic_provider_requests_structured_output():
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return anthropic_reply()

    result = anthropic_provider(create).complete_json(**CALL)
    assert (result.data, result.input_tokens, result.output_tokens) == ({"a": 1}, 10, 2)
    assert seen["model"] == "claude-test"
    assert seen["max_tokens"] == 5000
    assert seen["system"] == "sys"
    assert seen["messages"] == [{"role": "user", "content": "prompt"}]
    assert seen["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


@pytest.mark.parametrize("stop_reason, message", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_anthropic_provider_rejects_incomplete_answers(stop_reason, message):
    with pytest.raises(LLMError, match=message):
        anthropic_provider(lambda **k: anthropic_reply(stop_reason=stop_reason)).complete_json(**CALL)


def sdk_error(module, cls, status):
    request = httpx2.Request("POST", "https://api.example.test/v1")
    return getattr(module, cls)("err", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    "cls, status, message",
    [
        ("AuthenticationError", 401, "rejected the API key"),
        ("PermissionDeniedError", 403, "can't use this model"),
        ("NotFoundError", 404, "doesn't know the model 'claude-test'"),
        ("RateLimitError", 429, "rate-limiting"),
        ("InternalServerError", 500, r"HTTP 500"),
    ],
)
def test_anthropic_sdk_errors_become_user_messages(cls, status, message):
    def create(**kwargs):
        raise sdk_error(anthropic, cls, status)

    with pytest.raises(LLMError, match=message) as info:
        anthropic_provider(create).complete_json(**CALL)
    assert "sk-ant-test" not in str(info.value)


def test_anthropic_connection_error():
    def create(**kwargs):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.example.test"))

    with pytest.raises(LLMError, match="Couldn't reach the Anthropic API"):
        anthropic_provider(create).complete_json(**CALL)


def test_anthropic_key_check_reports_rejected_key():
    def retrieve(*args, **kwargs):
        raise sdk_error(anthropic, "AuthenticationError", 401)

    with pytest.raises(LLMError, match="Anthropic rejected this API key"):
        anthropic_provider(retrieve).check()


# ----------------------------------------------------------------------- OpenAI provider


def openai_provider(model, create):
    provider = llm.OpenAIProvider("sk-openai-test", model)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider


def openai_reply(content='{"a": 2}', refusal=None, finish_reason="stop", usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=refusal), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4) if usage else None,
    )


@pytest.mark.parametrize("model, reasoning", [("gpt-5.4-mini", True), ("o3", True), ("gpt-4o-mini", False)])
def test_openai_provider_requests_strict_schema(model, reasoning):
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return openai_reply()

    result = openai_provider(model, create).complete_json(**CALL)
    assert (result.data, result.input_tokens, result.output_tokens) == ({"a": 2}, 8, 4)
    assert seen["response_format"]["json_schema"] == {"name": "thing", "schema": SCHEMA, "strict": True}
    assert seen["max_completion_tokens"] == 5000
    assert ("reasoning_effort" in seen) is reasoning


def test_openai_provider_handles_refusal_truncation_and_missing_usage():
    with pytest.raises(LLMError, match="declined"):
        openai_provider("gpt-4o", lambda **k: openai_reply(refusal="no")).complete_json(**CALL)
    with pytest.raises(LLMError, match="cut off"):
        openai_provider("gpt-4o", lambda **k: openai_reply(finish_reason="length")).complete_json(**CALL)
    result = openai_provider("gpt-4o", lambda **k: openai_reply(usage=False)).complete_json(**CALL)
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_openai_sdk_errors_become_user_messages():
    def create(**kwargs):
        raise sdk_error(openai, "RateLimitError", 429)

    with pytest.raises(LLMError, match="out of credit"):
        openai_provider("gpt-4o", create).complete_json(**CALL)


def test_check_key_uses_the_configured_model(monkeypatch):
    checked = []
    monkeypatch.setattr(settings, "openai_model", "gpt-custom")
    monkeypatch.setattr(llm.OpenAIProvider, "check", lambda self: checked.append(self.model))
    llm.check_key("openai", "sk-new")
    assert checked == ["gpt-custom"]
