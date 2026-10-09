import os
import shutil
import tempfile

# Point the app at a throwaway data dir before any app module reads settings.
# DATABASE_URL is pinned too, so a value from the shell or .env can't select a
# real database that teardown would then drop.
TEST_DATA_DIR = tempfile.mkdtemp(prefix="ara-test-")
TEST_DB_PATH = os.path.join(TEST_DATA_DIR, "app.db")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["SEMANTIC_SCHOLAR_API_KEY"] = ""
# No real LLM keys: a test that wants an LLM gets the `fake_llm` fixture.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "anthropic"

import re  # noqa: E402
import zlib  # noqa: E402

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import embeddings, http, llm  # noqa: E402

assert engine.url.database == TEST_DB_PATH, f"tests must not use {engine.url}"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)
    shutil.rmtree(settings.pdf_dir, ignore_errors=True)


EMPTY_ARXIV_FEED = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


class FakeNetwork:
    """Answers every outbound request the app makes; nothing reaches the internet.

    Register handlers per host with `network.on(host, handler)`. Semantic Scholar
    answers 404 and arXiv an empty feed unless a test says otherwise; other
    unregistered hosts fail the test.
    """

    def __init__(self):
        self.handlers = {
            "api.semanticscholar.org": lambda r: httpx.Response(404, json={}),
            "export.arxiv.org": lambda r: httpx.Response(200, content=EMPTY_ARXIV_FEED),
        }
        self.requests: list[httpx.Request] = []
        self.unexpected: list[str] = []

    def on(self, host: str, handler) -> None:
        self.handlers[host] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if handler := self.handlers.get(request.url.host):
            return handler(request)
        self.unexpected.append(str(request.url))
        return httpx.Response(599)


@pytest.fixture(autouse=True)
def network(monkeypatch):
    fake = FakeNetwork()
    def mock_client(timeout=10.0):
        return httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=True)

    monkeypatch.setattr(http, "make_client", mock_client)
    monkeypatch.setattr(http, "make_download_client", mock_client)
    yield fake
    assert not fake.unexpected, f"unexpected HTTP requests: {fake.unexpected}"


class FakeLLM:
    """Stands in for an LLM provider. `answers` maps a schema name to the JSON to return,
    or to an exception to raise; an unanswered call fails like a real LLM error.
    Every call is recorded."""

    def __init__(self, name: str = "anthropic", model: str = "fake-model"):
        self.name = name
        self.model = model
        self.answers: dict[str, dict | Exception] = {}
        self.calls: list[dict] = []

    def complete_json(self, *, system, prompt, schema, schema_name, max_tokens):
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": schema, "schema_name": schema_name}
        )
        answer = self.answers.get(schema_name)
        if answer is None:
            raise llm.LLMError(f"FakeLLM has no answer for {schema_name}")
        if isinstance(answer, Exception):
            raise answer
        return llm.LLMResult(data=answer, input_tokens=1234, output_tokens=321)


@pytest.fixture()
def fake_llm(monkeypatch):
    """Makes a fake provider the only configured LLM (as "anthropic")."""
    fake = FakeLLM()
    monkeypatch.setattr(llm, "available", lambda: {fake.name: fake})
    return fake


class FakeEmbedder:
    """Stands in for the local embedding model: hashed bag-of-words vectors, so texts
    that share words are similar. Nothing is downloaded. Every call is recorded."""

    dim = 64
    STOPWORDS = {"the", "and", "how", "what", "does", "this", "that", "are", "for", "our", "with"}

    def __init__(self):
        self.calls: list[list[str]] = []
        self.error: Exception | None = None

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in zip(out, texts):
            for word in re.findall(r"[a-z]{3,}", text.removeprefix(embeddings.QUERY_PREFIX).lower()):
                if word not in self.STOPWORDS:
                    row[zlib.crc32(word.encode()) % self.dim] += 1
            if (norm := np.linalg.norm(row)) > 0:
                row /= norm
        return out


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "get_embedder", lambda: fake)
    return fake
