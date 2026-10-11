import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import create_app


@pytest.fixture()
def serve(tmp_path):
    """Run the app against a given frontend build directory."""
    clients = []

    def start(dist_dir):
        client = TestClient(create_app(frontend_dist=dist_dir))
        clients.append(client.__enter__())
        return client

    yield start
    for client in clients:
        client.__exit__(None, None, None)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def built_frontend(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    return dist


def test_built_frontend_is_served_at_root(serve, built_frontend):
    client = serve(built_frontend)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "<div id=root>" in resp.text
    assert client.get("/assets/app.js").text == "console.log('app')"


def test_api_and_docs_still_reach_fastapi_when_frontend_is_served(serve, built_frontend):
    client = serve(built_frontend)

    assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}
    assert client.get("/docs").status_code == 200
    assert client.get("/api/no-such-route").status_code == 404


def test_without_a_build_only_the_api_is_served(serve, tmp_path):
    client = serve(tmp_path / "missing")

    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200
