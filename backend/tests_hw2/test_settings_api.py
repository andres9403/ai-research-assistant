"""Tests for the settings endpoints (routers/settings.py) and API key storage (services/api_keys.py).

`.env` writes go to a per-test temp file (see conftest), never the repository's `.env`.
"""

import stat

import httpx
import pytest

from app import config
from app.config import settings
from app.services import api_keys, http, llm
from app.services.llm import LLMError

from tests_hw2.helpers import mock_client

VALID_KEY = "sk-test_" + "a" * 30


@pytest.fixture
def key_checks(monkeypatch):
    """Accept every LLM key check, recording (provider, key)."""
    calls = []
    monkeypatch.setattr(llm, "check_key", lambda name, key: calls.append((name, key)))
    return calls


def s2_answers(monkeypatch, response):
    seen = []

    def handler(request):
        seen.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(http, "make_client", lambda timeout=10.0: mock_client(handler))
    return seen


# ----------------------------------------------------------------------- LLM settings


def test_llm_settings_with_nothing_configured(client):
    body = client.get("/api/settings/llm").json()
    assert body == {"default": None, "providers": [], "ollama": {"model": "gemma3:4b", "running": False}}


def test_llm_settings_lists_keyed_providers_without_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", VALID_KEY)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    response = client.get("/api/settings/llm")
    body = response.json()
    assert body["default"] == "anthropic"  # the configured default has no key
    assert body["providers"] == [
        {"name": "anthropic", "label": "Anthropic Claude", "model": llm.DEFAULT_MODELS["anthropic"]}
    ]
    assert VALID_KEY not in response.text


def test_saving_an_llm_key_checks_it_stores_it_and_enables_the_provider(client, key_checks):
    response = client.put("/api/settings/llm/keys/openai", json={"key": f"  {VALID_KEY}  "})
    assert response.status_code == 200
    assert key_checks == [("openai", VALID_KEY)]
    assert [p["name"] for p in response.json()["providers"]] == ["openai"]
    assert response.json()["default"] == "openai"
    assert VALID_KEY not in response.text
    assert settings.openai_api_key == VALID_KEY
    assert f"OPENAI_API_KEY={VALID_KEY}" in config.ENV_FILE.read_text().splitlines()


@pytest.mark.parametrize(
    "key",
    ["short", "has spaces in the middle of it ok", "a" * 401, VALID_KEY + "\nLLM_PROVIDER=openai", "ключ" * 10],
)
def test_malformed_keys_are_422_and_not_saved(client, key_checks, key):
    response = client.put("/api/settings/llm/keys/anthropic", json={"key": key})
    assert response.status_code == 422
    assert "doesn't look like an API key" in response.json()["detail"]
    assert key not in response.text
    assert key_checks == []
    assert not config.ENV_FILE.exists()
    assert settings.anthropic_api_key in (None, "")


def test_rejected_key_is_400_and_not_saved(client, monkeypatch):
    def reject(name, key):
        raise LLMError("Anthropic rejected this API key.")

    monkeypatch.setattr(llm, "check_key", reject)
    response = client.put("/api/settings/llm/keys/anthropic", json={"key": VALID_KEY})
    assert response.status_code == 400
    assert response.json()["detail"] == "Anthropic rejected this API key."
    assert not config.ENV_FILE.exists()
    assert not settings.anthropic_api_key


@pytest.mark.parametrize("provider", ["ollama", "semantic_scholar", "gemini"])
def test_llm_key_endpoint_only_accepts_keyed_llm_providers(client, key_checks, provider):
    assert client.put(f"/api/settings/llm/keys/{provider}", json={"key": VALID_KEY}).status_code == 422
    assert key_checks == []
    assert not config.ENV_FILE.exists()


def test_key_body_is_required(client, key_checks):
    assert client.put("/api/settings/llm/keys/openai", json={}).status_code == 422
    assert key_checks == []
    assert not config.ENV_FILE.exists()


def test_removing_an_llm_key(client, key_checks):
    client.put("/api/settings/llm/keys/anthropic", json={"key": VALID_KEY})
    response = client.delete("/api/settings/llm/keys/anthropic")
    assert response.status_code == 200
    assert response.json()["providers"] == []
    assert settings.anthropic_api_key is None
    assert "ANTHROPIC_API_KEY=" in config.ENV_FILE.read_text().splitlines()
    assert VALID_KEY not in config.ENV_FILE.read_text()


# ----------------------------------------------------------------------- search settings


def test_search_key_lifecycle(client, monkeypatch):
    seen = s2_answers(monkeypatch, httpx.Response(200, json={"paperId": "x"}))
    assert client.get("/api/settings/search").json() == {"semantic_scholar_key": False}

    response = client.put("/api/settings/search/keys/semantic_scholar", json={"key": VALID_KEY})
    assert response.status_code == 200
    assert response.json() == {"semantic_scholar_key": True}
    assert seen[0].headers["x-api-key"] == VALID_KEY
    assert f"SEMANTIC_SCHOLAR_API_KEY={VALID_KEY}" in config.ENV_FILE.read_text()

    assert client.delete("/api/settings/search/keys/semantic_scholar").json() == {"semantic_scholar_key": False}
    assert settings.semantic_scholar_api_key is None


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(403), "rejected this API key"),
        (httpx.Response(401), "rejected this API key"),
        (httpx.Response(429), "rate-limiting"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.ConnectError("offline"), "Couldn't reach Semantic Scholar"),
    ],
)
def test_search_key_check_failures_are_400(client, monkeypatch, response, message):
    s2_answers(monkeypatch, response)
    result = client.put("/api/settings/search/keys/semantic_scholar", json={"key": VALID_KEY})
    assert result.status_code == 400
    assert message in result.json()["detail"]
    assert not config.ENV_FILE.exists()
    assert client.get("/api/settings/search").json() == {"semantic_scholar_key": False}


def test_search_key_endpoint_rejects_other_providers(client, monkeypatch):
    seen = s2_answers(monkeypatch, httpx.Response(200))
    assert client.put("/api/settings/search/keys/anthropic", json={"key": VALID_KEY}).status_code == 422
    assert seen == []
    assert not config.ENV_FILE.exists()
    assert settings.anthropic_api_key in (None, "")


def test_malformed_search_key_is_422_without_contacting_semantic_scholar(client, monkeypatch):
    seen = s2_answers(monkeypatch, httpx.Response(200))
    response = client.put("/api/settings/search/keys/semantic_scholar", json={"key": "bad key"})
    assert response.status_code == 422
    assert "doesn't look like an API key" in response.json()["detail"]
    assert seen == []
    assert not config.ENV_FILE.exists()


# ----------------------------------------------------------------------- write_env


def test_write_env_replaces_first_assignment_and_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nLLM_PROVIDER=openai\nexport OPENAI_API_KEY=old\nOTHER=1\nOPENAI_API_KEY=older\n")
    api_keys.write_env(env, "OPENAI_API_KEY", "new")
    assert env.read_text() == "# comment\nLLM_PROVIDER=openai\nOPENAI_API_KEY=new\nOTHER=1\n"


def test_write_env_appends_and_creates_owner_only_file(tmp_path):
    env = tmp_path / ".env"
    api_keys.write_env(env, "ANTHROPIC_API_KEY", "abc")
    assert env.read_text() == "ANTHROPIC_API_KEY=abc\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    api_keys.write_env(env, "OPENAI_API_KEY", "def")
    assert env.read_text() == "ANTHROPIC_API_KEY=abc\nOPENAI_API_KEY=def\n"
    assert not (tmp_path / ".env.tmp").exists()


def test_write_env_does_not_confuse_similar_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MY_OPENAI_API_KEY=keep\nOPENAI_API_KEY_OLD=keep\n")
    api_keys.write_env(env, "OPENAI_API_KEY", "x")
    assert env.read_text() == "MY_OPENAI_API_KEY=keep\nOPENAI_API_KEY_OLD=keep\nOPENAI_API_KEY=x\n"
