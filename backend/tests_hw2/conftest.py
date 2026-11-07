"""Fixtures for the hw2 suite.

The environment is pointed at a throwaway directory *before* any `app` module is
imported, so the real `data/` directory, database and API keys are never used.
Real network access is blocked for every test: any request that reaches httpx's
real transport fails the test.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hw2-tests-"))
os.environ.update(
    {
        "DATA_DIR": str(_TMP_ROOT / "data"),
        "DATABASE_URL": f"sqlite:///{_TMP_ROOT / 'test.db'}",
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": "",
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "",
        "OLLAMA_URL": "",  # blank turns Ollama detection off
        "OLLAMA_MODEL": "",
        "OLLAMA_TIMEOUT": "900",
        "OLLAMA_NUM_CTX": "16384",
        "LLM_TIMEOUT": "90",
        "SEMANTIC_SCHOLAR_API_KEY": "",
        "CORS_ORIGINS": '["http://localhost:5173"]',
    }
)

import httpx  # noqa: E402
import httpx2  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import embeddings, llm  # noqa: E402

from tests_hw2.helpers import FakeEmbedder, FakeLLM  # noqa: E402

assert str(settings.data_dir).startswith(str(_TMP_ROOT)), "tests must not use the real data directory"
assert str(_TMP_ROOT) in settings.db_url


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


# ----------------------------------------------------------------------- safety nets


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any test whose code reaches a real network transport."""
    attempts: list[str] = []

    def refuse(self, request):
        attempts.append(str(request.url))
        raise httpx.ConnectError("network disabled in tests", request=request)

    def refuse_sdk(self, request):  # httpx2 is what the Anthropic/OpenAI SDKs use
        attempts.append(str(request.url))
        raise httpx2.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse_sdk)
    yield attempts
    assert not attempts, f"test tried to reach the real network: {attempts}"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Restore `settings` after each test and point `.env` writes at a temp file."""
    snapshot = dict(settings.__dict__)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    llm._build.cache_clear()
    yield
    settings.__dict__.clear()
    settings.__dict__.update(snapshot)
    llm._build.cache_clear()


@event.listens_for(engine, "connect")
def _fast_sqlite(dbapi_connection, _record):
    # Test-only speed-up: a throwaway database needs no durability.
    dbapi_connection.execute("PRAGMA synchronous=OFF")
    dbapi_connection.execute("PRAGMA journal_mode=MEMORY")


@pytest.fixture(scope="session", autouse=True)
def schema():
    init_db()


@pytest.fixture(autouse=True)
def fresh_db(schema):
    """Empty tables and an empty PDF directory for every test."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    shutil.rmtree(settings.pdf_dir, ignore_errors=True)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch) -> FakeEmbedder:
    """Never download or run the real embedding model."""
    embedder = FakeEmbedder()
    monkeypatch.setattr(embeddings, "get_embedder", lambda: embedder)
    return embedder


# ----------------------------------------------------------------------- app fixtures


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    """Make a fake provider the only configured LLM (named "anthropic")."""
    provider = FakeLLM()
    monkeypatch.setattr(llm, "available", lambda ollama=None: {"anthropic": provider})
    return provider
