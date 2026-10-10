import math
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Paper
from app.services import pdf
from tests.conftest import TEST_DB_PATH
from tests.pdf_factory import ABSTRACT, AUTHORS, TITLE, make_paper_pdf, make_scanned_pdf
from tests.test_papers import paper as paper_payload

PDF = make_paper_pdf()
PDF_HOST = "pdfs.example.org"


def upload(client, data=PDF, name="paper.pdf"):
    return client.post("/api/papers/upload", files={"file": (name, data, "application/pdf")})


def test_upload_extracts_metadata_and_chunks(client, network):
    resp = upload(client)
    assert resp.status_code == 201
    paper = resp.json()
    assert paper["source"] == "upload"
    assert paper["title"] == TITLE
    assert paper["authors"] == AUTHORS
    assert paper["year"] == 2024
    assert paper["abstract"] == ABSTRACT
    assert paper["url"] == "https://arxiv.org/abs/2401.01234"
    assert paper["status"] == "ready"
    assert paper["has_pdf"] is True
    assert paper["page_count"] == 4
    assert paper["metadata_source"] == "pdf"
    assert "pdf_path" not in paper

    # Semantic Scholar was asked first by arXiv id, then by title; then arXiv by id,
    # then by title phrase and title words. Nothing matched, so the PDF's metadata stands.
    asked = [(r.url.host, r.url.path, r.url.params.get("id_list") or r.url.params.get("search_query"))
             for r in network.requests]
    assert asked == [
        ("api.semanticscholar.org", "/graph/v1/paper/arXiv:2401.01234", None),
        ("api.semanticscholar.org", "/graph/v1/paper/search/match", None),
        ("export.arxiv.org", "/api/query", "2401.01234"),
        ("export.arxiv.org", "/api/query", 'ti:"sparse expert routing for efficient language models"'),
        ("export.arxiv.org", "/api/query", "ti:sparse AND ti:expert AND ti:routing AND ti:efficient AND ti:language AND ti:models"),
    ]

    chunks = client.get(f"/api/papers/{paper['id']}/chunks").json()
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))
    assert {c["section"] for c in chunks} >= {"Abstract", "Introduction", "Method", "Results"}
    assert "References" not in {c["section"] for c in chunks}

    pdf = client.get(f"/api/papers/{paper['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content == PDF

    # The upload shows up in the library like a search result.
    assert client.get("/api/papers", params={"source": "upload"}).json()[0]["title"] == TITLE


def test_upload_uses_a_semantic_scholar_match(client, network):
    s2 = {
        "paperId": "s2abc",
        "title": "Sparse Expert Routing for Efficient Language Models",
        "authors": [{"name": "Ana María López"}, {"name": "Wei Zhang"}, {"name": "John O'Neil"}],
        "year": 2023,
        "abstract": "Published abstract.",
        "url": "https://www.semanticscholar.org/paper/s2abc",
        "externalIds": {"DOI": "10.1000/Sparse", "ArXiv": "2401.01234"},
    }
    network.on("api.semanticscholar.org", lambda r: httpx.Response(200, json=s2))
    paper = upload(client).json()
    assert paper["metadata_source"] == "semantic_scholar"
    assert paper["year"] == 2023
    assert paper["doi"] == "10.1000/sparse"
    assert paper["abstract"] == "Published abstract."
    assert paper["external_id"] == "s2abc"


def arxiv_feed(arxiv_id, title, year=2024):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/{arxiv_id}v1</id><title>{title}</title>
      <link href="https://arxiv.org/abs/{arxiv_id}v1" rel="alternate" type="text/html"/>
      <summary>The arXiv abstract.</summary><published>{year}-01-03T00:00:00Z</published>
      <author><name>Ana María López</name></author><author><name>Wei Zhang</name></author>
    </entry></feed>""".encode()


def test_upload_falls_back_to_arxiv_by_id(client, network):
    network.on("api.semanticscholar.org", lambda r: httpx.Response(429))
    network.on("export.arxiv.org", lambda r: httpx.Response(200, content=arxiv_feed("2401.01234", TITLE)))
    paper = upload(client).json()
    assert paper["metadata_source"] == "arxiv"
    assert paper["abstract"] == "The arXiv abstract."
    assert paper["external_id"] == "2401.01234"


def test_upload_matched_by_arxiv_title_search_is_flagged(client, network):
    def arxiv(request):
        if "id_list" in request.url.params:
            return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        return httpx.Response(200, content=arxiv_feed("2401.09999", TITLE.upper()))

    network.on("export.arxiv.org", arxiv)
    paper = upload(client, make_paper_pdf(arxiv_stamp=None)).json()
    assert paper["metadata_source"] == "arxiv_title"  # the UI shows a verify warning for this
    assert paper["url"] == "https://arxiv.org/abs/2401.09999v1"
    assert paper["year"] == 2024


def test_upload_matched_on_arxiv_attaches_to_the_saved_arxiv_paper(client, network):
    network.on("export.arxiv.org", lambda r: httpx.Response(200, content=arxiv_feed("2401.01234", TITLE)))
    saved = client.post(
        "/api/papers",
        json=paper_payload(source="arxiv", external_id="2401.01234", doi=None, title="Preprint title",
                           pdf_url=None),
    ).json()
    resp = upload(client)
    assert resp.status_code == 200
    assert resp.json()["id"] == saved["id"]


def test_upload_matching_a_saved_paper_attaches_to_it(client):
    saved = client.post(
        "/api/papers",
        json=paper_payload(title=TITLE.upper(), doi=None, abstract=None, pdf_url=None),
    ).json()
    assert saved["status"] == "no_pdf"

    resp = upload(client)
    assert resp.status_code == 200
    attached = resp.json()
    assert attached["id"] == saved["id"]
    assert attached["title"] == TITLE.upper()  # saved metadata wins
    assert attached["abstract"] == ABSTRACT  # but blanks are filled in
    assert attached["status"] == "ready" and attached["has_pdf"]
    assert len(client.get("/api/papers").json()) == 1

    # A second upload of the same paper is a duplicate.
    resp = upload(client)
    assert resp.status_code == 409
    assert resp.json()["detail"]["paper_id"] == saved["id"]


def test_scanned_upload_is_kept_but_marked_failed(client):
    resp = upload(client, make_scanned_pdf(), name="my_scanned-paper.pdf")
    assert resp.status_code == 201
    paper = resp.json()
    assert paper["title"] == "my scanned-paper"
    assert paper["status"] == "failed"
    assert "scanned" in paper["status_detail"]
    assert paper["has_pdf"] is True


def test_reuploading_a_failed_pdf_is_a_duplicate(client):
    first = upload(client, make_scanned_pdf(), name="my_scanned-paper.pdf").json()
    assert first["status"] == "failed"

    resp = upload(client, make_scanned_pdf(), name="my_scanned-paper.pdf")
    assert resp.status_code == 409
    assert resp.json()["detail"]["paper_id"] == first["id"]
    assert len(client.get("/api/papers").json()) == 1


@pytest.mark.parametrize(
    "data, status",
    [(b"", 422), (b"<html></html>", 422), (b"%PDF-1.4 garbage", 422)],
    ids=["empty", "not-pdf", "corrupt"],
)
def test_bad_uploads_are_rejected(client, data, status):
    resp = upload(client, data)
    assert resp.status_code == status
    assert client.get("/api/papers").json() == []


def test_oversized_upload_is_413(client, monkeypatch):
    from app.routers import upload as upload_router

    monkeypatch.setattr(upload_router, "MAX_PDF_BYTES", 1000)
    assert upload(client).status_code == 413


def test_attach_pdf_to_saved_paper(client):
    saved = client.post("/api/papers", json=paper_payload(abstract=None, pdf_url=None)).json()
    resp = client.post(
        f"/api/papers/{saved['id']}/pdf", files={"file": ("x.pdf", PDF, "application/pdf")}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["title"] == saved["title"]
    assert body["abstract"] == ABSTRACT
    assert client.get(f"/api/papers/{saved['id']}/chunks").json()

    bad = client.post(f"/api/papers/{saved['id']}/pdf", files={"file": ("x.pdf", b"nope")})
    assert bad.status_code == 422
    assert client.post("/api/papers/999/pdf", files={"file": ("x.pdf", PDF)}).status_code == 404


def test_saving_a_paper_downloads_its_open_access_pdf(client, network):
    network.on(PDF_HOST, lambda r: httpx.Response(200, content=PDF))
    resp = client.post("/api/papers", json=paper_payload(pdf_url=f"https://{PDF_HOST}/a.pdf"))
    assert resp.status_code == 201
    assert resp.json()["status"] == "downloading"

    # TestClient runs background tasks before returning, so the download is done.
    paper = client.get(f"/api/papers/{resp.json()['id']}").json()
    assert paper["status"] == "ready"
    assert paper["page_count"] == 4
    assert paper["title"] == "Attention Is All You Need"  # saved metadata is not replaced
    assert len(client.get(f"/api/papers/{paper['id']}/chunks").json()) > 3


@pytest.mark.parametrize(
    "response, reason",
    [
        (httpx.Response(403), "HTTP 403"),
        (httpx.Response(200, content=b"<html>Publisher landing page</html>"), "web page"),
        (httpx.Response(200, content=b"%PDF-1.4 broken"), "could not be read"),
    ],
    ids=["forbidden", "landing-page", "corrupt"],
)
def test_failed_download_leaves_an_actionable_message(client, network, response, reason):
    network.on(PDF_HOST, lambda r: response)
    saved = client.post("/api/papers", json=paper_payload(pdf_url=f"https://{PDF_HOST}/a.pdf")).json()
    paper = client.get(f"/api/papers/{saved['id']}").json()
    assert paper["status"] == "no_pdf"
    assert reason in paper["status_detail"]
    assert "Upload the PDF" in paper["status_detail"]
    assert paper["has_pdf"] is False


def test_paper_without_pdf_link_is_not_downloaded(client, network):
    saved = client.post("/api/papers", json=paper_payload(pdf_url=None)).json()
    assert saved["status"] == "no_pdf"
    assert network.requests == []
    assert client.post(f"/api/papers/{saved['id']}/pdf/fetch").status_code == 409
    assert client.get(f"/api/papers/{saved['id']}/pdf").status_code == 404


def test_fetch_retries_a_failed_download(client, network):
    responses = iter([httpx.Response(503), httpx.Response(200, content=PDF)])
    network.on(PDF_HOST, lambda r: next(responses))
    saved = client.post("/api/papers", json=paper_payload(pdf_url=f"https://{PDF_HOST}/a.pdf")).json()
    assert client.get(f"/api/papers/{saved['id']}").json()["status"] == "no_pdf"

    resp = client.post(f"/api/papers/{saved['id']}/pdf/fetch")
    assert resp.status_code == 202
    assert client.get(f"/api/papers/{saved['id']}").json()["status"] == "ready"


def test_non_http_pdf_links_are_refused(client):
    saved = client.post("/api/papers", json=paper_payload(pdf_url="file:///etc/passwd")).json()
    paper = client.get(f"/api/papers/{saved['id']}").json()
    assert paper["status"] == "no_pdf"
    assert "http(s)" in paper["status_detail"]


def test_edit_metadata(client):
    paper = upload(client).json()
    resp = client.patch(
        f"/api/papers/{paper['id']}",
        json={"title": " Edited Title ", "authors": ["A. One", " ", "B. Two"], "year": 2021,
              "doi": "https://doi.org/10.5555/EDIT", "abstract": "  "},
    )
    assert resp.status_code == 200
    edited = resp.json()
    assert edited["title"] == "Edited Title"
    assert edited["authors"] == ["A. One", "B. Two"]
    assert edited["year"] == 2021
    assert edited["doi"] == "10.5555/edit"
    assert edited["abstract"] is None
    assert edited["metadata_source"] == "edited"
    assert edited["status"] == "ready"  # editing doesn't touch the PDF
    assert client.get(f"/api/papers/{paper['id']}").json() == edited


def test_edit_rejects_bad_values_and_duplicates(client):
    first = client.post("/api/papers", json=paper_payload(pdf_url=None)).json()
    second = upload(client).json()
    url = f"/api/papers/{second['id']}"

    assert client.patch(url, json={"title": ""}).status_code == 422
    assert client.patch(url, json={"year": 99999}).status_code == 422
    resp = client.patch(url, json={"title": first["title"]})
    assert resp.status_code == 409
    assert resp.json()["detail"]["paper_id"] == first["id"]
    assert client.get(url).json()["title"] == TITLE  # unchanged
    assert client.patch(url, json={"title": TITLE}).status_code == 200  # own title is fine
    assert client.patch("/api/papers/999", json={"year": 2020}).status_code == 404


def test_delete_removes_pdf_and_chunks(client):
    paper = upload(client).json()
    path = settings.pdf_dir / f"{paper['id']}.pdf"
    assert path.is_file()

    assert client.delete(f"/api/papers/{paper['id']}").status_code == 204
    assert not path.exists()
    with sqlite3.connect(TEST_DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone() == (0,)


def test_restart_recovers_interrupted_downloads(client):
    saved = client.post("/api/papers", json=paper_payload(pdf_url=None)).json()
    with SessionLocal() as session:
        session.get(Paper, saved["id"]).status = "downloading"
        session.commit()
    client.__exit__(None, None, None)

    with TestClient(app) as restarted:
        paper = restarted.get(f"/api/papers/{saved['id']}").json()
    assert paper["status"] == "no_pdf"
    assert "restart" in paper["status_detail"]


def test_restart_re_estimates_chunk_tokens(client):
    """Chunks stored under the old 4-characters-per-token estimate get the current one."""
    paper_id = upload(client).json()["id"]
    with SessionLocal() as session:
        chunks = session.get(Paper, paper_id).chunks
        expected = {c.id: pdf.estimate_tokens(c.text) for c in chunks}
        for chunk in chunks:
            chunk.n_tokens = math.ceil(len(chunk.text) / 4)
        session.commit()
    client.__exit__(None, None, None)

    with TestClient(app) as restarted:
        stored = {c["id"]: c["n_tokens"] for c in restarted.get(f"/api/papers/{paper_id}/chunks").json()}
    assert stored == expected


def test_m1_database_gets_new_columns(client):
    client.__exit__(None, None, None)
    Base.metadata.drop_all(engine)
    with sqlite3.connect(TEST_DB_PATH) as conn:  # the papers table exactly as M1 created it
        conn.execute(
            "CREATE TABLE papers (id INTEGER PRIMARY KEY, source VARCHAR(16), external_id VARCHAR(128),"
            " doi VARCHAR(256), title TEXT, authors JSON, year INTEGER, abstract TEXT, url TEXT,"
            " pdf_url TEXT, pdf_path TEXT, status VARCHAR(16), created_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO papers (source, title, authors, status, created_at)"
            " VALUES ('s2', 'Old Paper', '[]', 'no_pdf', '2026-10-01 00:00:00')"
        )

    with TestClient(app) as restarted:
        papers = restarted.get("/api/papers").json()
        assert [p["title"] for p in papers] == ["Old Paper"]
        assert papers[0]["status_detail"] is None and papers[0]["has_pdf"] is False
        assert upload(restarted).status_code == 201  # chunks table was created too
