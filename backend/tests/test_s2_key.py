"""Semantic Scholar key entered in the app (issue #12): checked, saved to `.env`, used at once, never returned.

The conftest `env_file` fixture points `.env` at a temp file and `network` answers every request.
"""

import re

import httpx
import pytest

from app.config import Settings, settings
from app.services import pipeline, search
from tests.pdf_factory import make_paper_pdf

S2_KEY = "S2abcdefghijklmnopqrstuvwxyz0123456789AB"
S2_HOST = "api.semanticscholar.org"
EMPTY_FEED = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def put_key(client, key):
    return client.put("/api/settings/search/keys/semantic_scholar", json={"key": key})


def accept(key):
    """A Semantic Scholar stand-in that accepts only `key` and records each request's key."""
    seen = []

    def handle(request):
        seen.append((request.url.path, request.headers.get("x-api-key")))
        if request.headers.get("x-api-key") != key:
            return httpx.Response(403, json={"message": "Forbidden"})
        if request.url.path.endswith("/paper/search"):
            return httpx.Response(200, json={"data": [{"paperId": "p1", "title": "Found with a key"}]})
        return httpx.Response(200, json={"paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776"})

    return handle, seen


def test_key_is_checked_saved_and_used_by_search_at_once(client, network, env_file):
    env_file.write_text("ANTHROPIC_API_KEY=\nSEMANTIC_SCHOLAR_API_KEY=\n")
    handle, seen = accept(S2_KEY)
    network.on(S2_HOST, handle)
    assert client.get("/api/settings/search").json() == {"semantic_scholar_key": False}

    resp = put_key(client, f" {S2_KEY}\n")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"semantic_scholar_key": True}
    assert S2_KEY not in resp.text
    assert seen == [("/graph/v1/paper/arXiv:1706.03762", S2_KEY)]
    assert env_file.read_text() == f"ANTHROPIC_API_KEY=\nSEMANTIC_SCHOLAR_API_KEY={S2_KEY}\n"
    assert S2_KEY not in client.get("/api/settings/search").text

    found = client.get("/api/search", params={"q": "attention"}).json()
    assert found["provider"] == "s2"
    assert seen[-1] == ("/graph/v1/paper/search", S2_KEY)


def test_saved_key_loads_back_through_the_settings_loader(client, network, env_file, monkeypatch):
    """A hand-edited and an app-saved key are the same thing after a restart."""
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY")
    network.on(S2_HOST, accept(S2_KEY)[0])
    assert put_key(client, S2_KEY).status_code == 200
    assert Settings(_env_file=env_file).semantic_scholar_api_key == S2_KEY


def test_hand_edited_key_is_still_used(client, network, monkeypatch):
    handle, seen = accept(S2_KEY)
    network.on(S2_HOST, handle)
    monkeypatch.setattr(settings, "semantic_scholar_api_key", S2_KEY)
    assert client.get("/api/settings/search").json() == {"semantic_scholar_key": True}
    assert client.get("/api/search", params={"q": "attention"}).json()["provider"] == "s2"
    assert seen == [("/graph/v1/paper/search", S2_KEY)]


@pytest.mark.parametrize(
    "status, message",
    [
        (403, "^Semantic Scholar rejected this API key.$"),
        (401, "^Semantic Scholar rejected this API key.$"),
        (429, "rate-limiting"),
        (500, r"HTTP 500"),
    ],
)
def test_key_that_is_not_confirmed_is_reported_and_not_saved(client, network, env_file, status, message):
    env_file.write_text("SEMANTIC_SCHOLAR_API_KEY=\n")
    network.on(S2_HOST, lambda r: httpx.Response(status, json={"message": "x"}))

    resp = put_key(client, S2_KEY)

    assert resp.status_code == 400
    assert re.search(message, resp.json()["detail"])
    assert S2_KEY not in resp.text
    assert env_file.read_text() == "SEMANTIC_SCHOLAR_API_KEY=\n"
    assert not settings.semantic_scholar_api_key


def test_unreachable_semantic_scholar_is_reported_and_not_saved(client, network, env_file):
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    network.on(S2_HOST, down)
    resp = put_key(client, S2_KEY)
    assert resp.status_code == 400
    assert "Couldn't reach Semantic Scholar" in resp.json()["detail"]
    assert not env_file.exists()


def test_text_that_is_not_a_key_is_refused_before_any_request(client, network, env_file):
    resp = put_key(client, f"{S2_KEY}\nLLM_PROVIDER=openai")
    assert resp.status_code == 422
    assert S2_KEY not in resp.text
    assert network.requests == []
    assert not env_file.exists()


def test_unknown_search_provider_is_refused(client, network):
    assert client.put("/api/settings/search/keys/arxiv", json={"key": S2_KEY}).status_code == 422
    assert client.delete("/api/settings/search/keys/arxiv").status_code == 422
    assert network.requests == []


def test_removed_key_is_blanked_and_unused(client, network, env_file):
    handle, seen = accept(S2_KEY)
    network.on(S2_HOST, handle)
    assert put_key(client, S2_KEY).status_code == 200

    resp = client.delete("/api/settings/search/keys/semantic_scholar")

    assert resp.json() == {"semantic_scholar_key": False}
    assert env_file.read_text() == "SEMANTIC_SCHOLAR_API_KEY=\n"
    client.get("/api/search", params={"q": "attention"})
    assert seen[-1] == ("/graph/v1/paper/search", None)


def test_saved_key_is_used_by_the_upload_metadata_lookup(client, network, monkeypatch):
    """The upload pipeline reads the key per request, so a key saved in the app applies at once."""
    used = []
    monkeypatch.setattr(pipeline, "lookup", lambda client, meta, key=None: used.append(key) or meta)
    network.on(S2_HOST, accept(S2_KEY)[0])
    assert put_key(client, S2_KEY).status_code == 200

    resp = client.post("/api/papers/upload", files={"file": ("p.pdf", make_paper_pdf(), "application/pdf")})
    assert resp.status_code == 201, resp.text
    assert used == [S2_KEY]


# The 429 hint on the search results.


def rate_limited(request):
    return httpx.Response(429, json={"message": "Too Many Requests"})


def test_keyless_429_fallback_suggests_a_key(client, network):
    network.on(S2_HOST, rate_limited)
    data = client.get("/api/search", params={"q": "attention"}).json()
    assert data["provider"] == "arxiv"
    assert data["fallback_reason"] == "Semantic Scholar unavailable (HTTP 429)"
    assert data["suggest_s2_key"] is True


def test_429_with_a_key_set_does_not_suggest_one(client, network, monkeypatch):
    monkeypatch.setattr(settings, "semantic_scholar_api_key", S2_KEY)
    network.on(S2_HOST, rate_limited)
    assert client.get("/api/search", params={"q": "attention"}).json()["suggest_s2_key"] is False


@pytest.mark.parametrize(
    "s2",
    [
        lambda r: httpx.Response(500, json={}),
        lambda r: httpx.Response(200, json={"data": []}),
    ],
)
def test_other_fallbacks_do_not_suggest_a_key(client, network, s2):
    network.on(S2_HOST, s2)
    data = client.get("/api/search", params={"q": "attention"}).json()
    assert data["provider"] == "arxiv"
    assert data["suggest_s2_key"] is False


def test_check_key_sends_the_key_header_only():
    seen = []

    def handle(request):
        seen.append((str(request.url), request.headers.get("x-api-key")))
        return httpx.Response(200, json={"paperId": "x"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        search.check_key(http_client, S2_KEY)
    assert seen == [(f"{search.S2_KEY_CHECK_URL}?fields=paperId", S2_KEY)]
