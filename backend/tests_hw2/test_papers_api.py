"""HTTP tests for the library endpoints in routers/papers.py and the health check."""

import pytest

from app.models import ChatMessage, Chunk, Paper, Summary
from fastapi.testclient import TestClient

from app.config import settings
from app.db import engine
from app.main import create_app

from tests_hw2.helpers import SUMMARY_DATA, add_paper, add_ready_paper

NEW_PAPER = {
    "source": "s2",
    "external_id": "abc123",
    "doi": "https://doi.org/10.1000/XYZ",
    "title": "A Study of Widgets",
    "authors": ["Alice Smith", "Bob Jones"],
    "year": 2022,
    "abstract": "Widgets are studied.",
    "url": "https://example.org/paper",
}


def test_health_reports_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_unknown_api_route_is_404(client):
    assert client.get("/api/does-not-exist").status_code == 404


# ----------------------------------------------------------------------- create


def test_save_paper_returns_201_and_the_stored_paper(client):
    response = client.post("/api/papers", json=NEW_PAPER)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["title"] == "A Study of Widgets"
    assert body["doi"] == "10.1000/xyz"  # normalised
    assert body["authors"] == ["Alice Smith", "Bob Jones"]
    assert body["status"] == "no_pdf"  # no pdf_url, so nothing to download
    assert body["has_pdf"] is False
    assert body["created_at"].endswith(("Z", "+00:00"))
    assert client.get(f"/api/papers/{body['id']}").json() == body


def test_save_duplicate_paper_is_409_with_existing_id(client):
    first = client.post("/api/papers", json=NEW_PAPER).json()
    dup = {**NEW_PAPER, "source": "arxiv", "external_id": None, "doi": None, "title": "a study of widgets."}
    response = client.post("/api/papers", json=dup)
    assert response.status_code == 409
    assert response.json()["detail"] == {"message": "This paper is already in your library", "paper_id": first["id"]}
    assert len(client.get("/api/papers").json()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "upload", "title": "Uploads use another endpoint"},
        {"source": "s2", "title": ""},
        {"source": "s2"},
        {"title": "No source"},
        {"source": "s2", "title": "Bad year", "year": "nineteen"},
        {"source": "s2", "title": "Bad authors", "authors": "not a list"},
    ],
)
def test_save_paper_validates_payload(client, payload):
    assert client.post("/api/papers", json=payload).status_code == 422
    assert client.get("/api/papers").json() == []


@pytest.mark.parametrize("title", ["   ", "\t\n ", "\u00a0\u2003"])
def test_save_paper_rejects_whitespace_only_title(client, title):
    assert client.post("/api/papers", json={"source": "s2", "title": title}).status_code == 422
    assert client.get("/api/papers").json() == []


def test_save_paper_preserves_nonblank_title(client):
    title = "  A Study of Widgets  "
    response = client.post("/api/papers", json={"source": "s2", "title": title})
    assert response.status_code == 201
    assert response.json()["title"] == title
    assert client.get(f"/api/papers/{response.json()['id']}").json()["title"] == title


def test_legacy_blank_title_remains_readable(client, session):
    paper = add_paper(session, title="   ")
    response = client.get("/api/papers")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "   "
    assert client.get(f"/api/papers/{paper.id}").status_code == 200


def test_saved_library_survives_a_restart(client, session):
    saved = client.post("/api/papers", json=NEW_PAPER).json()
    paper = session.get(Paper, saved["id"])
    paper.summary = Summary(provider="openai", model="m", content=SUMMARY_DATA, sections=[], input_tokens=1,
                            output_tokens=1)
    paper.messages = [ChatMessage(role="user", content="kept question")]
    session.commit()
    session.close()

    engine.dispose()  # drop every connection, as a stopped server would
    with TestClient(create_app()) as restarted:
        assert restarted.get(f"/api/papers/{saved['id']}").json() == saved
        assert [m["content"] for m in restarted.get(f"/api/papers/{saved['id']}/chat").json()] == ["kept question"]
        assert restarted.get(f"/api/papers/{saved['id']}/summary").json()["provider"] == "openai"


# ----------------------------------------------------------------------- read / list


def test_get_missing_paper_is_404(client):
    response = client.get("/api/papers/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Paper not found"


def test_paper_id_must_be_an_integer(client):
    assert client.get("/api/papers/abc").status_code == 422


def test_list_papers_with_query_and_filters(client, session):
    add_paper(session, title="Graph Neural Networks", year=2018, source="s2")
    add_paper(session, title="Language Models", year=2023, source="arxiv", authors=["Grace Hopper"])
    assert [p["title"] for p in client.get("/api/papers").json()] == ["Language Models", "Graph Neural Networks"]
    assert [p["title"] for p in client.get("/api/papers", params={"q": "hopper"}).json()] == ["Language Models"]
    assert [p["title"] for p in client.get("/api/papers", params={"source": "s2"}).json()] == [
        "Graph Neural Networks"
    ]
    assert [p["title"] for p in client.get("/api/papers", params={"year_from": 2020}).json()] == ["Language Models"]
    assert [p["title"] for p in client.get("/api/papers", params={"year_from": 2018, "year_to": 2018}).json()] == [
        "Graph Neural Networks"
    ]
    assert client.get("/api/papers", params={"year_to": 2017}).json() == []


def test_list_papers_query_length_limit(client, session):
    add_paper(session, title="x" * 300)
    assert len(client.get("/api/papers", params={"q": "x" * 300}).json()) == 1  # 300 characters is allowed
    assert client.get("/api/papers", params={"q": "x" * 301}).status_code == 422


# ----------------------------------------------------------------------- update


def test_patch_updates_only_given_fields(client, session):
    paper = add_paper(session, title="Original", year=2019, abstract="Keep this")
    response = client.patch(f"/api/papers/{paper.id}", json={"title": "Renamed", "year": 2020})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed"
    assert body["year"] == 2020
    assert body["abstract"] == "Keep this"
    assert body["metadata_source"] == "edited"


@pytest.mark.parametrize("title", ["   ", "\t\n ", "\u00a0\u2003"])
def test_patch_ignores_whitespace_only_title(client, session, title):
    paper = add_paper(session, title="Original")
    response = client.patch(f"/api/papers/{paper.id}", json={"title": title})
    assert response.status_code == 200
    assert response.json()["title"] == "Original"
    assert client.get(f"/api/papers/{paper.id}").json()["title"] == "Original"


@pytest.mark.parametrize("payload", [{"year": 999}, {"year": 2101}, {"title": ""}, {"authors": "Ann"}])
def test_patch_validates_fields(client, session, payload):
    paper = add_paper(session)
    before = client.get(f"/api/papers/{paper.id}").json()
    assert client.patch(f"/api/papers/{paper.id}", json=payload).status_code == 422
    assert client.get(f"/api/papers/{paper.id}").json() == before  # nothing applied, not marked edited


def test_patch_to_a_duplicate_title_is_409(client, session):
    other = add_paper(session, title="Existing Title")
    paper = add_paper(session, title="Mine")
    response = client.patch(f"/api/papers/{paper.id}", json={"title": "EXISTING title"})
    assert response.status_code == 409
    assert response.json()["detail"]["paper_id"] == other.id
    assert client.get(f"/api/papers/{paper.id}").json()["title"] == "Mine"


def test_patch_missing_paper_is_404(client):
    assert client.patch("/api/papers/42", json={"title": "x"}).status_code == 404


@pytest.mark.parametrize("year", [1000, 2100])
def test_patch_accepts_years_at_the_bounds(client, session, year):
    paper = add_paper(session)
    response = client.patch(f"/api/papers/{paper.id}", json={"year": year})
    assert response.status_code == 200
    assert response.json()["year"] == year


# ----------------------------------------------------------------------- delete


def test_delete_removes_paper_its_pdf_and_dependents(client, session):
    paper = add_ready_paper(session)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf = settings.pdf_dir / f"{paper.id}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    paper.pdf_path = pdf.name
    paper.summary = Summary(
        provider="anthropic", model="m", content={}, sections=[], input_tokens=1, output_tokens=1
    )
    paper.messages = [ChatMessage(role="user", content="hi")]
    session.commit()
    paper_id = paper.id

    assert client.delete(f"/api/papers/{paper_id}").status_code == 204
    assert not pdf.exists()
    assert client.get(f"/api/papers/{paper_id}").status_code == 404
    session.expire_all()
    assert session.query(Chunk).count() == 0
    assert session.query(Summary).count() == 0
    assert session.query(ChatMessage).count() == 0
    assert client.delete(f"/api/papers/{paper_id}").status_code == 404


def test_delete_tolerates_missing_pdf_file(client, session):
    paper = add_paper(session, pdf_path="gone.pdf")
    assert client.delete(f"/api/papers/{paper.id}").status_code == 204
    assert session.query(Paper).count() == 0


# ----------------------------------------------------------------------- pdf / chunks


def test_get_pdf_without_file_is_404(client, session):
    paper = add_paper(session)
    response = client.get(f"/api/papers/{paper.id}/pdf")
    assert response.status_code == 404
    assert response.json()["detail"] == "This paper has no PDF"


def test_get_pdf_with_dangling_path_is_404(client, session):
    paper = add_paper(session, pdf_path="missing.pdf")
    response = client.get(f"/api/papers/{paper.id}/pdf")
    assert response.status_code == 404
    assert response.json()["detail"] == "This paper has no PDF"


def test_get_pdf_serves_the_stored_file(client, session):
    paper = add_paper(session)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    (settings.pdf_dir / "stored.pdf").write_bytes(b"%PDF-1.4 content")
    paper.pdf_path = "stored.pdf"
    session.commit()
    response = client.get(f"/api/papers/{paper.id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF-1.4 content"


def test_list_chunks_in_paper_order(client, session):
    paper = add_ready_paper(session)
    chunks = client.get(f"/api/papers/{paper.id}/chunks").json()
    assert [c["ordinal"] for c in chunks] == [0, 1, 2, 3, 4]
    assert chunks[0]["section"] == "Abstract"
    assert set(chunks[0]) == {"id", "ordinal", "section", "page_start", "page_end", "n_tokens", "text"}


def test_list_chunks_empty_for_paper_without_pdf(client, session):
    paper = add_paper(session)
    assert client.get(f"/api/papers/{paper.id}/chunks").json() == []


def test_fetch_pdf_without_open_access_link_is_409(client, session):
    paper = add_paper(session, pdf_url=None)
    response = client.post(f"/api/papers/{paper.id}/pdf/fetch")
    assert response.status_code == 409
    assert "no open-access PDF link" in response.json()["detail"]


def test_fetch_pdf_while_busy_is_409(client, session):
    paper = add_paper(session, pdf_url="https://example.org/p.pdf", status="downloading")
    response = client.post(f"/api/papers/{paper.id}/pdf/fetch")
    assert response.status_code == 409
    assert "already being processed" in response.json()["detail"]
