"""Startup and infrastructure: upgrading an older database, serving the built frontend, and the
local embedding model wrapper (with fastembed replaced, so no model is downloaded)."""

import sys
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import settings
from app.db import engine, init_db
from app.main import app, create_app
from app.models import ChatMessage
from app.services import embeddings
from app.services.embeddings import EmbeddingError, FastEmbedder

from tests_hw2.helpers import add_paper

# ----------------------------------------------------------------------- schema upgrade


@pytest.fixture
def old_chat_table():
    """Replace chat_messages with the shape an early version created (no background/provider/model/tokens)."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE chat_messages"))
        conn.execute(text(
            "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, paper_id INTEGER, role VARCHAR(16), "
            "content TEXT, citations JSON, created_at DATETIME)"
        ))
    yield
    ChatMessage.__table__.drop(engine)  # restore the current schema for later tests
    ChatMessage.__table__.create(engine)


def test_startup_adds_columns_missing_from_an_older_database(old_chat_table, session):
    paper = add_paper(session)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO chat_messages (paper_id, role, content, citations, created_at) "
                 "VALUES (:pid, 'user', 'an old question', '[]', '2024-01-01 00:00:00.000000')"),
            {"pid": paper.id},
        )
    assert "background" not in {c["name"] for c in inspect(engine).get_columns("chat_messages")}

    with TestClient(app) as client:  # startup runs init_db, which upgrades the table
        columns = {c["name"] for c in inspect(engine).get_columns("chat_messages")}
        assert {"background", "provider", "model", "input_tokens", "output_tokens"} <= columns
        # the old row survives and reads through the API with the new fields empty
        [message] = client.get(f"/api/papers/{paper.id}/chat").json()
        assert message["content"] == "an old question"
        assert message["background"] is None and message["provider"] is None
    init_db()  # running it again on an up-to-date schema is a no-op
    assert {c["name"] for c in inspect(engine).get_columns("chat_messages")} == columns


# ----------------------------------------------------------------------- frontend


def test_app_without_a_frontend_build_serves_only_the_api(tmp_path):
    with TestClient(create_app(frontend_dist=tmp_path)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").json()["status"] == "ok"


def test_built_frontend_is_served_at_root_without_hiding_the_api(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Research</title>")
    with TestClient(create_app(frontend_dist=tmp_path)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "<title>Research</title>" in root.text
        assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}
        assert client.get("/api/papers").json() == []


# ----------------------------------------------------------------------- FastEmbedder


@pytest.fixture
def fake_fastembed(monkeypatch):
    """A stand-in `fastembed` module that records how TextEmbedding is created and used."""
    state = {"inits": [], "fail_init": False, "fail_embed": False, "batches": []}

    class TextEmbedding:
        def __init__(self, model_name, cache_dir):
            if state["fail_init"]:
                raise OSError("download failed")
            state["inits"].append((model_name, cache_dir))

        def embed(self, texts, batch_size):
            if state["fail_embed"]:
                raise RuntimeError("onnx crashed")
            state["batches"].append((list(texts), batch_size))
            for i, _ in enumerate(texts):
                yield [float(i), 1.0, 0.0]

    module = types.ModuleType("fastembed")
    module.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return state


def test_fast_embedder_loads_the_model_lazily_once_into_the_data_dir(fake_fastembed):
    embedder = FastEmbedder()
    assert fake_fastembed["inits"] == []  # nothing loaded until first use
    vectors = embedder.embed(["a", "b"])
    embedder.embed(["c"])
    assert fake_fastembed["inits"] == [(embeddings.MODEL_NAME, str(settings.model_dir))]
    assert settings.model_dir.is_dir()
    assert str(settings.model_dir).startswith(str(settings.data_dir))
    assert vectors.dtype == np.float32
    assert vectors.shape == (2, 3)
    assert fake_fastembed["batches"][0] == (["a", "b"], 32)


def test_fast_embedder_load_failure_is_a_user_facing_error_and_can_be_retried(fake_fastembed):
    embedder = FastEmbedder()
    fake_fastembed["fail_init"] = True
    with pytest.raises(EmbeddingError, match="couldn't be loaded"):
        embedder.embed(["a"])
    fake_fastembed["fail_init"] = False
    assert embedder.embed(["a"]).shape == (1, 3)  # a later call tries the load again


def test_fast_embedder_runtime_failure_is_an_embedding_error(fake_fastembed):
    embedder = FastEmbedder()
    fake_fastembed["fail_embed"] = True
    with pytest.raises(EmbeddingError, match="failed while reading the paper"):
        embedder.embed(["a"])


def test_default_embedder_matches_the_stored_vector_size():
    assert isinstance(embeddings._embedder, FastEmbedder)
    assert embeddings._embedder.dim == 384  # bge-small-en-v1.5
