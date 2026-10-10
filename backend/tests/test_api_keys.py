"""API keys entered in the app (issue #8): checked with the provider, saved to `.env`, never returned.

The conftest `env_file` fixture points `.env` at a temp file, so the repo's real one is never touched.
"""

import os
import stat

import anthropic
import httpx2
import openai
import pytest

from app import config
from app.config import Settings, settings
from app.services import api_keys, llm

ANT_KEY = "sk-ant-api03-" + "a" * 40
OAI_KEY = "sk-proj-" + "b" * 40


@pytest.fixture()
def checked(monkeypatch):
    """Accepts every key and records what was checked; set `.error` to reject."""

    class Checker:
        calls: list[tuple[str, str]] = []
        error: Exception | None = None

        def __call__(self, name, key):
            self.calls.append((name, key))
            if self.error:
                raise self.error

    checker = Checker()
    checker.calls = []
    monkeypatch.setattr(llm, "check_key", checker)
    return checker


def put_key(client, provider, key):
    return client.put(f"/api/settings/llm/keys/{provider}", json={"key": key})


def test_saved_key_is_checked_written_to_env_and_used_at_once(client, checked, env_file):
    env_file.write_text("# my settings\nLLM_PROVIDER=openai\nANTHROPIC_API_KEY=\nSEMANTIC_SCHOLAR_API_KEY=s2\n")

    resp = put_key(client, "anthropic", f"  {ANT_KEY}\n")

    assert resp.status_code == 200, resp.text
    assert checked.calls == [("anthropic", ANT_KEY)]
    assert env_file.read_text() == (
        f"# my settings\nLLM_PROVIDER=openai\nANTHROPIC_API_KEY={ANT_KEY}\nSEMANTIC_SCHOLAR_API_KEY=s2\n"
    )
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
    data = resp.json()
    assert [p["name"] for p in data["providers"]] == ["anthropic"]
    assert ANT_KEY not in resp.text
    assert llm.resolve().name == "anthropic"


def test_saved_file_loads_back_through_the_settings_loader(client, checked, env_file, monkeypatch):
    """What `.env` gets must be what config.py reads after a restart. (Shell variables
    override `.env`, so the conftest's blank ones are cleared for this restart.)"""
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert put_key(client, "openai", OAI_KEY).status_code == 200
    assert put_key(client, "anthropic", ANT_KEY).status_code == 200
    restarted = Settings(_env_file=env_file)
    assert (restarted.openai_api_key, restarted.anthropic_api_key) == (OAI_KEY, ANT_KEY)


def test_key_is_appended_and_duplicates_collapse(env_file):
    env_file.write_text("OPENAI_API_KEY=old\nX=1\nexport OPENAI_API_KEY=older\n")
    api_keys.write_env(env_file, "OPENAI_API_KEY", "new")
    api_keys.write_env(env_file, "ANTHROPIC_API_KEY", "k")
    assert env_file.read_text() == "OPENAI_API_KEY=new\nX=1\nANTHROPIC_API_KEY=k\n"


def test_missing_env_file_is_created_owner_only(client, checked, env_file):
    assert not env_file.exists()
    assert put_key(client, "openai", OAI_KEY).status_code == 200
    assert env_file.read_text() == f"OPENAI_API_KEY={OAI_KEY}\n"
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600


def test_rejected_key_changes_nothing(client, checked, env_file):
    env_file.write_text("ANTHROPIC_API_KEY=\n")
    checked.error = llm.LLMError("Anthropic rejected this API key.")

    resp = put_key(client, "anthropic", ANT_KEY)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Anthropic rejected this API key."
    assert env_file.read_text() == "ANTHROPIC_API_KEY=\n"
    assert settings.anthropic_api_key in (None, "")


@pytest.mark.parametrize(
    "text",
    [
        f"{ANT_KEY}\nLLM_PROVIDER=openai",  # would add a second setting to .env
        f"{ANT_KEY} {ANT_KEY}",
        "sk-short",
        "",
    ],
)
def test_text_that_is_not_a_key_is_refused_without_echoing_it(client, checked, env_file, text):
    resp = put_key(client, "anthropic", text)
    assert resp.status_code == 422
    assert "doesn't look like an API key" in resp.json()["detail"]
    assert text.strip() == "" or text.strip() not in resp.text
    assert checked.calls == []
    assert not env_file.exists()


def test_unknown_provider_is_refused(client, checked):
    assert put_key(client, "ollama", ANT_KEY).status_code == 422
    assert client.delete("/api/settings/llm/keys/gemini").status_code == 422
    assert checked.calls == []


def test_removed_key_is_blanked_and_unused(client, checked, env_file):
    assert put_key(client, "anthropic", ANT_KEY).status_code == 200

    resp = client.delete("/api/settings/llm/keys/anthropic")

    assert resp.status_code == 200
    assert resp.json()["providers"] == []
    assert env_file.read_text() == "ANTHROPIC_API_KEY=\n"
    with pytest.raises(llm.LLMNotConfigured):
        llm.resolve("anthropic")


# The provider check itself, through the real SDKs against a mocked transport.


def sdk_client(sdk, handler):
    return sdk(api_key="test-key", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def error(status, kind):
    return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": "x"}})


def test_anthropic_check_retrieves_the_configured_model():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, request.headers.get("x-api-key")))
        return httpx2.Response(
            200,
            json={"id": "claude-haiku-5-5", "type": "model", "display_name": "Claude Haiku", "created_at": "2026-01-01T00:00:00Z"},
        )

    provider = llm.AnthropicProvider(ANT_KEY, "claude-haiku-5-5")
    provider.client = anthropic.Anthropic(
        api_key=ANT_KEY, max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handle))
    )
    provider.check()
    assert seen == [("GET", "/v1/models/claude-haiku-5-5", ANT_KEY)]


@pytest.mark.parametrize(
    "response, message",
    [
        (error(401, "authentication_error"), "^Anthropic rejected this API key.$"),
        (error(403, "permission_error"), "can't use this model"),
        (error(404, "not_found_error"), "ANTHROPIC_MODEL"),
    ],
)
def test_anthropic_check_failures(response, message):
    provider = llm.AnthropicProvider("k", "claude-haiku-5-5")
    provider.client = sdk_client(anthropic.Anthropic, lambda r: response)
    with pytest.raises(llm.LLMError, match=message):
        provider.check()


def test_openai_check_retrieves_the_configured_model():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path))
        return httpx2.Response(200, json={"id": "gpt-5.4-mini", "object": "model", "created": 0, "owned_by": "openai"})

    provider = llm.OpenAIProvider(OAI_KEY, "gpt-5.4-mini")
    provider.client = sdk_client(openai.OpenAI, handle)
    provider.check()
    assert seen == [("GET", "/v1/models/gpt-5.4-mini")]


def test_openai_check_rejects_a_bad_key():
    provider = llm.OpenAIProvider("k", "gpt-5.4-mini")
    provider.client = sdk_client(
        openai.OpenAI, lambda r: httpx2.Response(401, json={"error": {"message": "bad", "type": "invalid_request_error"}})
    )
    with pytest.raises(llm.LLMError, match="^OpenAI rejected this API key.$"):
        provider.check()


def test_check_key_uses_the_model_override(monkeypatch):
    built = []

    class Probe:
        def __init__(self, key, model):
            built.append((key, model))

        def check(self):
            pass

    monkeypatch.setattr(settings, "openai_model", "gpt-4.1-mini")
    monkeypatch.setattr(llm, "OpenAIProvider", Probe)
    llm.check_key("openai", OAI_KEY)
    assert built == [(OAI_KEY, "gpt-4.1-mini")]


def test_env_file_fixture_never_points_at_the_repo():
    assert config.ENV_FILE != config.REPO_ROOT / ".env"
