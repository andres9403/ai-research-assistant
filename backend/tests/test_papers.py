import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.library import normalize_doi, normalize_title


def paper(**overrides):
    data = {
        "source": "s2",
        "external_id": "s2-1",
        "doi": "10.1000/abc",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "José Pérez"],
        "year": 2017,
        "abstract": "We propose the Transformer.",
        "url": "https://example.org/s2-1",
        "pdf_url": None,
    }
    return {**data, **overrides}


def test_save_and_get(client):
    resp = client.post("/api/papers", json=paper())
    assert resp.status_code == 201
    saved = resp.json()
    assert saved["id"] > 0
    assert saved["status"] == "no_pdf"
    assert saved["authors"] == ["Ashish Vaswani", "José Pérez"]

    fetched = client.get(f"/api/papers/{saved['id']}").json()
    assert fetched == saved


def test_saved_paper_survives_restart(client):
    saved = client.post("/api/papers", json=paper()).json()
    client.__exit__(None, None, None)  # shut the app down (runs lifespan teardown)

    with TestClient(app) as restarted:
        titles = [p["title"] for p in restarted.get("/api/papers").json()]
    assert titles == [saved["title"]]


@pytest.mark.parametrize(
    "duplicate",
    [
        paper(source="arxiv", external_id="other", doi="https://doi.org/10.1000/ABC", title="X"),
        paper(doi=None, title="Something else"),
        paper(source="arxiv", external_id="1706.03762", doi=None, title="attention is all you need!"),
    ],
    ids=["same-doi", "same-source-id", "same-normalised-title"],
)
def test_duplicates_are_rejected(client, duplicate):
    original = client.post("/api/papers", json=paper()).json()

    resp = client.post("/api/papers", json=duplicate)
    assert resp.status_code == 409
    assert resp.json()["detail"]["paper_id"] == original["id"]
    assert len(client.get("/api/papers").json()) == 1


def test_same_id_from_different_source_is_not_a_duplicate(client):
    client.post("/api/papers", json=paper())
    other = paper(source="arxiv", doi=None, title="A different paper")  # external_id "s2-1"
    assert client.post("/api/papers", json=other).status_code == 201


def test_list_newest_first_and_filters(client):
    client.post("/api/papers", json=paper())
    client.post(
        "/api/papers",
        json=paper(
            source="arxiv",
            external_id="2209.15001",
            doi=None,
            title="Dilated Neighborhood Attention Transformer",
            authors=["Ali Hassani"],
            year=2022,
            abstract="Hierarchical vision transformer with 100% more context.",
        ),
    )

    def titles(**params):
        resp = client.get("/api/papers", params=params)
        assert resp.status_code == 200
        return [p["title"] for p in resp.json()]

    assert titles() == ["Dilated Neighborhood Attention Transformer", "Attention Is All You Need"]
    assert titles(q="attention") == titles()
    assert titles(q="TRANSFORMER") == titles()  # title or abstract, case-insensitive
    assert titles(q="hassani") == ["Dilated Neighborhood Attention Transformer"]
    assert titles(q="josé") == ["Attention Is All You Need"]  # non-ASCII author names
    assert titles(q="100%") == ["Dilated Neighborhood Attention Transformer"]
    assert titles(q="10_%") == []  # LIKE wildcards are matched literally
    assert titles(source="s2") == ["Attention Is All You Need"]
    assert titles(year_from=2018) == ["Dilated Neighborhood Attention Transformer"]
    assert titles(year_to=2017) == ["Attention Is All You Need"]
    assert titles(year_from=2017, year_to=2022, q="dilated") == [
        "Dilated Neighborhood Attention Transformer"
    ]


def test_delete(client):
    saved = client.post("/api/papers", json=paper()).json()
    assert client.delete(f"/api/papers/{saved['id']}").status_code == 204
    assert client.get(f"/api/papers/{saved['id']}").status_code == 404
    assert client.get("/api/papers").json() == []
    # Once deleted, the same paper can be saved again.
    assert client.post("/api/papers", json=paper()).status_code == 201


def test_missing_paper_is_404(client):
    assert client.get("/api/papers/999").status_code == 404
    assert client.delete("/api/papers/999").status_code == 404


@pytest.mark.parametrize(
    "bad",
    [paper(title=""), paper(source="upload"), {"source": "s2"}],
    ids=["empty-title", "upload-source", "missing-title"],
)
def test_invalid_payload_is_422(client, bad):
    assert client.post("/api/papers", json=bad).status_code == 422


def test_normalisers():
    assert normalize_title("  Attention—Is All You Need?! ") == "attention is all you need"
    assert normalize_title("Über Café Models") == "uber cafe models"
    assert normalize_title("深度学习：综述") == "深度学习 综述"
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("doi:10.1/X") == "10.1/x"
    assert normalize_doi("") is None
