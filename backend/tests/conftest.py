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

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import http  # noqa: E402

assert engine.url.database == TEST_DB_PATH, f"tests must not use {engine.url}"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)
    shutil.rmtree(settings.pdf_dir, ignore_errors=True)


class FakeNetwork:
    """Answers every outbound request the app makes; nothing reaches the internet.

    Register handlers per host with `network.on(host, handler)`. Semantic Scholar
    answers 404 unless a test says otherwise; other unregistered hosts fail the test.
    """

    def __init__(self):
        self.handlers = {"api.semanticscholar.org": lambda r: httpx.Response(404, json={})}
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
    monkeypatch.setattr(
        http,
        "make_client",
        lambda timeout=10.0: httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=True),
    )
    yield fake
    assert not fake.unexpected, f"unexpected HTTP requests: {fake.unexpected}"
