"""Tests for PDF upload/attach (routers/upload.py) and the download pipeline (services/pipeline.py)."""

import socket

import httpcore
import httpx
import pytest

from app.config import settings
from app.main import app
from app.models import ChatMessage, Chunk, Paper, Summary
from app.routers import upload as upload_router
from app.services import http, pipeline
from app.services.http import BlockedAddress, get_http_client, public_addresses
from app.services.llm import LLMError
from app.services.pipeline import DownloadError, fetch_pdf

from tests_hw2.helpers import (
    PAPER_TITLE,
    FakeLLM,
    add_paper,
    add_ready_paper,
    encrypted_pdf,
    json_response,
    mock_client,
    paper_pdf,
    scanned_like_pdf,
)


@pytest.fixture
def offline_lookups(client):
    """Metadata lookups get 404s from Semantic Scholar and an empty arXiv feed."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        return httpx.Response(404)

    def override():
        with mock_client(handler) as c:
            yield c

    app.dependency_overrides[get_http_client] = override
    return seen


def upload(client, data, filename="paper.pdf", **form):
    return client.post("/api/papers/upload", files={"file": (filename, data, "application/pdf")}, data=form)


# ----------------------------------------------------------------------- upload


def test_upload_creates_a_ready_paper_with_extracted_metadata(client, session, offline_lookups, fake_embedder):
    data = paper_pdf()
    response = upload(client, data)
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "upload"
    assert body["title"] == PAPER_TITLE
    assert body["authors"] == ["Alice Smith", "Bob Jones"]
    assert body["status"] == "ready"
    assert body["has_pdf"] is True
    assert body["page_count"] == 2
    assert body["metadata_source"] == "pdf"
    assert (settings.pdf_dir / f"{body['id']}.pdf").read_bytes() == data
    assert offline_lookups, "metadata lookups should have been attempted (against the mock)"

    chunks = client.get(f"/api/papers/{body['id']}/chunks").json()
    assert {c["section"] for c in chunks} == {"Abstract", "Introduction", "Results", "Conclusion"}
    # the background task embedded every chunk
    stored = session.query(Chunk).filter_by(paper_id=body["id"]).all()
    assert stored and all(c.embedding is not None for c in stored)
    assert client.get(f"/api/papers/{body['id']}/pdf").content == data


def test_upload_uses_semantic_scholar_match(client):
    record = {
        "paperId": "s2-777",
        "title": "Deep Widgets for Robust Testing",
        "authors": [{"name": "Alice Smith"}],
        "year": 2021,
        "abstract": "Official.",
        "externalIds": {"DOI": "10.7/W"},
    }

    def handler(request):
        if "search/match" in str(request.url):
            return json_response({"data": [record]})
        return httpx.Response(404)

    app.dependency_overrides[get_http_client] = lambda: mock_client(handler)
    body = upload(client, paper_pdf()).json()
    assert body["metadata_source"] == "semantic_scholar_title"
    assert body["external_id"] == "s2-777"
    assert body["doi"] == "10.7/w"
    assert body["year"] == 2021


def test_upload_uses_llm_for_metadata_when_configured(client, offline_lookups, fake_llm):
    fake_llm.responses = [{"title": "LLM Corrected Title", "authors": ["Real Author"], "year": 2020}]
    body = upload(client, paper_pdf()).json()
    assert body["title"] == "LLM Corrected Title"
    assert body["authors"] == ["Real Author"]
    assert body["metadata_source"] == "llm"


def test_upload_survives_a_failing_llm(client, offline_lookups, fake_llm):
    fake_llm.responses = [LLMError("model down")]
    response = upload(client, paper_pdf())
    assert response.status_code == 201
    assert response.json()["title"] == PAPER_TITLE


def test_upload_title_falls_back_to_filename(client, offline_lookups):
    response = upload(client, scanned_like_pdf(), filename="my_great-paper v2.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "my great-paper v2"
    assert body["status"] == "failed"
    assert "OCR" in body["status_detail"]
    assert body["has_pdf"] is True


@pytest.mark.parametrize(
    "filename, expected",
    [("a_b_c.pdf", "a b c"), ("  .pdf", "Untitled paper"), (None, "Untitled paper"), ("x/y/deep_net.pdf", "deep net")],
)
def test_title_from_filename(filename, expected):
    assert pipeline._title_from_filename(filename) == expected


@pytest.mark.parametrize(
    "data, detail",
    [
        (b"just text, not a pdf", "File is not a PDF"),
        (b"%PDF-1.4 broken" + b"\x00" * 50, "PDF could not be read"),
    ],
)
def test_upload_rejects_unreadable_files(client, offline_lookups, data, detail):
    response = upload(client, data)
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert client.get("/api/papers").json() == []


def test_upload_rejects_password_protected_pdf(client, offline_lookups):
    response = upload(client, encrypted_pdf())
    assert response.status_code == 422
    assert "password" in response.json()["detail"]


def test_upload_rejects_empty_file(client, offline_lookups):
    response = upload(client, b"")
    assert response.status_code == 422
    assert response.json()["detail"] == "The uploaded file is empty"


def test_upload_rejects_oversized_file(client, offline_lookups, monkeypatch):
    monkeypatch.setattr(upload_router, "MAX_PDF_BYTES", 1024 * 1024)
    response = upload(client, b"%PDF-" + b"x" * (1024 * 1024))
    assert response.status_code == 413
    assert response.json()["detail"] == "PDFs are limited to 1 MB"


def test_upload_requires_a_file(client):
    assert client.post("/api/papers/upload").status_code == 422


def test_uploading_the_same_pdf_twice_is_409(client, offline_lookups):
    first = upload(client, paper_pdf()).json()
    response = upload(client, paper_pdf())
    assert response.status_code == 409
    assert response.json()["detail"]["paper_id"] == first["id"]
    assert len(client.get("/api/papers").json()) == 1


def test_upload_attaches_to_a_saved_paper_without_pdf(client, session, offline_lookups):
    saved = add_paper(session, title="Deep Widgets for Robust Testing", source="s2", authors=[], abstract=None)
    response = upload(client, paper_pdf())
    assert response.status_code == 200  # attached, not created
    body = response.json()
    assert body["id"] == saved.id
    assert body["source"] == "s2"
    assert body["status"] == "ready"
    assert body["authors"] == ["Alice Smith", "Bob Jones"]  # blank fields filled from the PDF
    assert body["year"] == 2020  # existing values kept
    assert len(client.get("/api/papers").json()) == 1


def test_upload_matching_a_paper_mid_download_is_409(client, session, offline_lookups):
    saved = add_paper(session, title=PAPER_TITLE, status="downloading")
    response = upload(client, paper_pdf())
    assert response.status_code == 409
    assert response.json()["detail"]["paper_id"] == saved.id


# ----------------------------------------------------------------------- attach


def attach(client, paper_id, data, **form):
    return client.post(f"/api/papers/{paper_id}/pdf", files={"file": ("p.pdf", data, "application/pdf")}, data=form)


def test_attach_pdf_processes_it_and_fills_only_blank_fields(client, session):
    paper = add_paper(session, title="My Own Title", authors=["Kept Author"], year=None, abstract=None)
    response = attach(client, paper.id, paper_pdf())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["has_pdf"] is True
    assert body["title"] == "My Own Title"
    assert body["authors"] == ["Kept Author"]
    assert body["abstract"].startswith("We study widgets")


def test_replacing_a_pdf_clears_summary_and_chat(client, session):
    paper = add_ready_paper(session)
    paper.summary = Summary(provider="anthropic", model="m", content={}, sections=[], input_tokens=1, output_tokens=1)
    paper.messages = [ChatMessage(role="user", content="old question")]
    session.commit()
    assert attach(client, paper.id, paper_pdf()).status_code == 200
    assert client.get(f"/api/papers/{paper.id}/summary").status_code == 404
    assert client.get(f"/api/papers/{paper.id}/chat").json() == []
    sections = {c["section"] for c in client.get(f"/api/papers/{paper.id}/chunks").json()}
    assert "Method" not in sections and "Results" in sections  # old chunks replaced


def test_attach_skips_llm_when_nothing_is_blank(client, session, fake_llm):
    paper = add_paper(session, authors=["A B"], year=2000, abstract="Has one.")
    assert attach(client, paper.id, paper_pdf()).status_code == 200
    assert fake_llm.calls == []


def test_attach_invalid_pdf_is_422_and_leaves_paper_unchanged(client, session):
    paper = add_paper(session)
    response = attach(client, paper.id, b"not a pdf")
    assert response.status_code == 422
    assert client.get(f"/api/papers/{paper.id}").json()["status"] == "no_pdf"


def test_attach_while_busy_is_409(client, session):
    paper = add_paper(session, status="processing")
    assert attach(client, paper.id, paper_pdf()).status_code == 409


def test_attach_to_missing_paper_is_404(client):
    assert attach(client, 12345, paper_pdf()).status_code == 404


def test_scanned_pdf_marks_paper_failed(client, session):
    paper = add_paper(session)
    body = attach(client, paper.id, scanned_like_pdf()).json()
    assert body["status"] == "failed"
    assert body["has_pdf"] is True


# ----------------------------------------------------------------------- open-access download


@pytest.fixture
def download_server(monkeypatch):
    """Serve PDF downloads from a dict url → response instead of the internet."""
    routes: dict[str, httpx.Response] = {}
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        response = routes.get(str(request.url), httpx.Response(404))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(http, "make_download_client", lambda timeout=30.0: mock_client(handler))
    return routes, seen


def test_saving_a_paper_with_pdf_url_downloads_and_processes_it(client, download_server):
    routes, seen = download_server
    routes["https://oa.example.org/widgets.pdf"] = httpx.Response(200, content=paper_pdf())
    response = client.post(
        "/api/papers", json={"source": "arxiv", "title": "Widgets", "pdf_url": "https://oa.example.org/widgets.pdf"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "downloading"
    paper = client.get(f"/api/papers/{response.json()['id']}").json()  # background task has run
    assert paper["status"] == "ready"
    assert paper["has_pdf"] is True
    assert paper["page_count"] == 2
    assert seen == ["https://oa.example.org/widgets.pdf"]


@pytest.mark.parametrize(
    "response, reason",
    [
        (httpx.Response(403), "the server answered HTTP 403"),
        (httpx.Response(200, content=b"<html>paywall</html>"), "the link leads to a web page, not a PDF file"),
        (httpx.ConnectTimeout("slow"), "the download failed (ConnectTimeout)"),
        (BlockedAddress("10.0.0.1"), "the link points to a private or local network address"),
        (httpx.Response(200, content=b"%PDF-1.4 corrupt" + b"\x00" * 20), "PDF could not be read"),
    ],
)
def test_failed_download_leaves_paper_without_pdf_and_explains(client, session, download_server, response, reason):
    routes, _ = download_server
    url = "https://oa.example.org/p.pdf"
    routes[url] = response
    paper = add_paper(session, pdf_url=url)
    assert client.post(f"/api/papers/{paper.id}/pdf/fetch").status_code == 202
    body = client.get(f"/api/papers/{paper.id}").json()
    assert body["status"] == "no_pdf"
    assert reason in body["status_detail"]
    assert pipeline.UPLOAD_HINT in body["status_detail"]
    assert body["has_pdf"] is False


def test_unexpected_pipeline_error_marks_paper_failed(client, session, download_server, monkeypatch):
    routes, _ = download_server
    routes["https://oa.example.org/p.pdf"] = httpx.Response(200, content=paper_pdf())
    paper = add_paper(session, pdf_url="https://oa.example.org/p.pdf")

    def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pipeline, "ingest", explode)
    client.post(f"/api/papers/{paper.id}/pdf/fetch")
    body = client.get(f"/api/papers/{paper.id}").json()
    assert body["status"] == "failed"
    assert "failed unexpectedly" in body["status_detail"]


def test_download_task_ignores_papers_not_queued(session, download_server):
    _, seen = download_server
    paper = add_paper(session, pdf_url="https://oa.example.org/p.pdf", status="no_pdf")
    pipeline.download_pdf(paper.id)
    pipeline.download_pdf(999999)  # deleted paper
    assert seen == []


@pytest.mark.xfail(
    strict=True,
    reason="SUSPECTED BUG: retrying the open-access download (POST /pdf/fetch) for a paper that is already "
    "processed and ready is allowed, and if that retry fails download_pdf sets status='no_pdf' although the "
    "stored PDF and chunks are untouched, so has_pdf is true but summary/Q&A answer 409 'full text isn't "
    "available'. recover_interrupted() shows the intended rule: a paper with chunks stays 'ready'.",
)
def test_failed_retry_keeps_an_already_processed_paper_ready(client, session, download_server):
    routes, _ = download_server
    routes["https://oa.example.org/p.pdf"] = httpx.Response(503)
    paper = add_ready_paper(session, pdf_url="https://oa.example.org/p.pdf", pdf_path="x.pdf")
    assert client.post(f"/api/papers/{paper.id}/pdf/fetch").status_code == 202
    body = client.get(f"/api/papers/{paper.id}").json()
    assert body["has_pdf"] is True
    assert len(client.get(f"/api/papers/{paper.id}/chunks").json()) == 5
    assert body["status"] == "ready"


def test_fetch_pdf_rejects_non_http_urls_without_a_request():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    for url in ("file:///etc/passwd", "ftp://x/y.pdf", "", None):
        with pytest.raises(DownloadError, match="not an http"):
            fetch_pdf(mock_client(handler), url)


def test_fetch_pdf_enforces_the_size_limit(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_PDF_BYTES", 1000)
    client = mock_client(lambda r: httpx.Response(200, content=b"%PDF-" + b"x" * 2000))
    with pytest.raises(DownloadError, match="larger than"):
        fetch_pdf(client, "https://example.org/big.pdf")


def test_fetch_pdf_follows_redirects_and_returns_bytes():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.org/final.pdf"})
        return httpx.Response(200, content=b"%PDF-1.7 data")

    assert fetch_pdf(mock_client(handler), "https://example.org/start") == b"%PDF-1.7 data"


# ----------------------------------------------------------------------- SSRF guard


def fake_resolver(addresses):
    def getaddrinfo(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    return getaddrinfo


def test_public_addresses_allows_public_hosts(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(["93.184.216.34", "93.184.216.34"]))
    assert public_addresses("example.org", 443) == ["93.184.216.34"]


@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["192.168.1.1"], ["93.184.216.34", "10.0.0.1"],
                  ["::ffff:127.0.0.1"], ["224.0.0.1"]]
)
def test_public_addresses_blocks_private_and_mixed_hosts(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(addresses))
    with pytest.raises(BlockedAddress):
        public_addresses("internal.example", 80)


def test_download_backend_refuses_loopback_before_connecting(monkeypatch):
    connects = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, port, *a, **k: connects.append(host))
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(["127.0.0.1"]))
    with pytest.raises(BlockedAddress):
        http.PublicNetworkBackend().connect_tcp("localhost", 8000)
    assert connects == []


def test_download_backend_connects_to_the_checked_ip(monkeypatch):
    connects = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, port, *a, **k: connects.append(host) or "stream")
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(["93.184.216.34"]))
    assert http.PublicNetworkBackend().connect_tcp("example.org", 443) == "stream"
    assert connects == ["93.184.216.34"]  # the validated address, not the name


def test_download_client_ignores_proxy_settings():
    with http.make_download_client() as client:
        assert client._trust_env is False
        assert isinstance(client._transport, http.PublicOnlyTransport)


# ----------------------------------------------------------------------- startup recovery


def test_startup_recovers_papers_left_mid_pipeline(session):
    stuck_empty = add_paper(session, title="Stuck Empty", status="downloading")
    stuck_ready = add_ready_paper(session, title="Stuck Ready")
    stuck_ready.status = "processing"
    session.commit()
    pipeline.recover_interrupted(session)
    session.expire_all()
    assert session.get(Paper, stuck_empty.id).status == "no_pdf"
    assert "restart" in session.get(Paper, stuck_empty.id).status_detail
    assert session.get(Paper, stuck_ready.id).status == "ready"
    assert session.get(Paper, stuck_ready.id).status_detail is None


def test_refresh_token_estimates_fixes_stale_counts(session):
    paper = add_ready_paper(session)
    for chunk in paper.chunks:
        chunk.n_tokens = 1
    session.commit()
    pipeline.refresh_token_estimates(session)
    session.expire_all()
    for chunk in session.query(Chunk).all():
        assert chunk.n_tokens == -(-len(chunk.text) // 3)


def test_queue_download_rules(session):
    no_link = add_paper(session, title="No Link")
    busy = add_paper(session, title="Busy", pdf_url="https://x/p.pdf", status="processing")
    ok = add_paper(session, title="Ok", pdf_url="https://x/p.pdf", status="failed", status_detail="old")
    assert pipeline.queue_download(session, no_link) is False
    assert pipeline.queue_download(session, busy) is False
    assert pipeline.queue_download(session, ok) is True
    assert (ok.status, ok.status_detail) == ("downloading", None)
