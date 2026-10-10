"""The local Ollama provider (issue #8): live detection, the chat request it sends, and its errors.

The conftest network mock answers for localhost, so no test reaches a real Ollama.
"""

import json

import httpx
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


def tags(*names):
    return lambda r: httpx.Response(200, json={"models": [{"name": n, "model": n} for n in names]})


def chat_reply(content='{"answer": "hi"}', done_reason="stop"):
    return {
        "model": "gemma3:4b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": 40,
        "eval_count": 7,
    }


def ollama_server(models=("gemma3:4b",), chat=None):
    """A handler for localhost that lists `models` and answers /api/chat with `chat`."""
    list_models = tags(*models)

    def handle(request):
        if request.url.path == "/api/tags":
            return list_models(request)
        if request.url.path == "/api/chat":
            return chat(request) if chat else httpx.Response(200, json=chat_reply())
        return httpx.Response(404, json={"error": "not found"})

    return handle


@pytest.fixture()
def no_keys(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")


def test_ollama_sends_schema_and_context_size(network, monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 12345)
    network.on("localhost", ollama_server())
    provider = llm.OllamaProvider("http://localhost:11434/", "gemma3:4b")

    result = call(provider)

    assert result.data == {"answer": "hi"}
    assert (result.input_tokens, result.output_tokens) == (40, 7)
    request = network.requests[-1]
    assert str(request.url) == "http://localhost:11434/api/chat"
    assert json.loads(request.content) == {
        "model": "gemma3:4b",
        "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hi."}],
        "format": SCHEMA,
        "stream": False,
        "options": {"num_ctx": 12345, "num_predict": 500},
    }


def test_ollama_output_is_capped(network):
    """A small model that loops would otherwise generate until OLLAMA_TIMEOUT on a CPU."""
    network.on("localhost", ollama_server())
    llm.OllamaProvider("http://localhost:11434", "gemma3:4b").complete_json(
        system="s", prompt="p", schema=SCHEMA, schema_name="greeting", max_tokens=8000
    )
    assert json.loads(network.requests[-1].content)["options"]["num_predict"] == 1024


def raise_timeout(request):
    raise httpx.ReadTimeout("slow", request=request)


def refuse(request):
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "chat, message",
    [
        (lambda r: httpx.Response(404, json={"error": "model 'gemma3:4b' not found"}), "ollama pull gemma3:4b"),
        (lambda r: httpx.Response(500, json={"error": "out of memory"}), r"HTTP 500"),
        (lambda r: httpx.Response(200, text="<html>"), "isn't JSON"),
        (lambda r: httpx.Response(200, json=chat_reply('{"ans', "length")), "cut off"),
        (lambda r: httpx.Response(200, json=chat_reply("not json")), "malformed JSON"),
        (raise_timeout, "OLLAMA_TIMEOUT"),
        (refuse, "Couldn't reach Ollama"),
    ],
)
def test_ollama_failures_become_readable_errors(network, chat, message):
    network.on("localhost", ollama_server(chat=chat))
    with pytest.raises(llm.LLMError, match=message):
        call(llm.OllamaProvider("http://localhost:11434", "gemma3:4b"))


def test_settings_offer_ollama_without_any_key(client, network, no_keys):
    network.on("localhost", ollama_server(models=("nomic-embed-text:latest", "gemma3:4b")))
    assert client.get("/api/settings/llm").json() == {
        "default": "ollama",
        "providers": [{"name": "ollama", "label": "Ollama (local)", "model": "gemma3:4b"}],
        "ollama": {"model": "gemma3:4b", "running": True},
    }
    assert llm.resolve().name == "ollama"


def test_ollama_needs_the_model_pulled(client, network, monkeypatch, no_keys):
    network.on("localhost", ollama_server(models=("mistral:latest",)))
    data = client.get("/api/settings/llm").json()
    assert data["providers"] == []
    assert data["ollama"] == {"model": "gemma3:4b", "running": True}
    with pytest.raises(llm.LLMNotConfigured, match="Ollama is running.*ollama pull gemma3:4b"):
        llm.resolve()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    with pytest.raises(llm.LLMNotConfigured, match="Ollama doesn't have gemma3:4b"):
        llm.resolve("ollama")


def test_stopped_ollama_explains_both_setup_paths(client, no_keys):
    data = client.get("/api/settings/llm").json()
    assert data == {"default": None, "providers": [], "ollama": {"model": "gemma3:4b", "running": False}}
    with pytest.raises(llm.LLMNotConfigured, match="Install Ollama.*ANTHROPIC_API_KEY"):
        llm.resolve()


def test_ollama_model_override_matches_untagged_names(client, network, monkeypatch, no_keys):
    monkeypatch.setattr(settings, "ollama_model", "gemma3")
    network.on("localhost", ollama_server(models=("gemma3:latest",)))
    assert [(p["name"], p["model"]) for p in client.get("/api/settings/llm").json()["providers"]] == [
        ("ollama", "gemma3")
    ]


def test_keyed_providers_stay_default_and_ollama_is_listed_last(client, network, monkeypatch, no_keys):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    network.on("localhost", ollama_server())
    data = client.get("/api/settings/llm").json()
    assert data["default"] == "openai"
    assert [p["name"] for p in data["providers"]] == ["openai", "ollama"]
    assert "sk-test" not in json.dumps(data)


def test_blank_ollama_url_turns_detection_off(client, network, monkeypatch, no_keys):
    monkeypatch.setattr(settings, "ollama_url", "")
    network.on("localhost", ollama_server())
    assert client.get("/api/settings/llm").json()["providers"] == []
    assert network.requests == []  # Ollama was never probed
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    with pytest.raises(llm.LLMNotConfigured, match="OLLAMA_URL is blank"):
        llm.resolve("ollama")


def test_summary_runs_on_ollama(client, network, no_keys):
    """End to end through the API: with no key set, a summary comes from the local model."""
    from tests.test_pdf_api import upload
    from tests.test_summaries import SUMMARY

    paper_id = upload(client).json()["id"]  # uploaded while Ollama is stopped
    network.on("localhost", ollama_server(chat=lambda r: httpx.Response(200, json=chat_reply(json.dumps(SUMMARY)))))

    response = client.post(f"/api/papers/{paper_id}/summary")

    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["provider"], data["model"], data["content"]["tldr"]) == ("ollama", "gemma3:4b", SUMMARY["tldr"])
    sent = json.loads(next(r for r in network.requests if r.url.path == "/api/chat").content)
    assert sent["options"]["num_ctx"] == settings.ollama_num_ctx
    assert "Sparse Expert Routing" in sent["messages"][1]["content"]
