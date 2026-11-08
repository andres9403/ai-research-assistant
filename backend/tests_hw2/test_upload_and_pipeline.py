"""Tests for PDF upload/attach (routers/upload.py) and the download pipeline (services/pipeline.py)."""

import socket

import httpcore
import httpx
import pymupdf
import pytest

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import ChatMessage, Chunk, Paper, Summary
from app.routers import upload as upload_router
from app.services import http, pipeline
from app.services.http import BlockedAddress, get_http_client, public_addresses
from app.services.llm import LLMError
from app.services.pipeline import DownloadError, fetch_pdf

from tests_hw2.helpers import (
    PAPER_TITLE,
    add_paper,
    add_ready_paper,
    build_pdf,
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


def pdf_with_doi_and_year() -> bytes:
    return paper_pdf(extra_first_page=[("Published in 2019. doi: 10.1145/12345.67890", 9, False)])


# ----------------------------------------------------------------------- upload


def test_upload_creates_a_ready_paper_with_extracted_metadata(client, session, offline_lookups, fake_embedder):
    data = pdf_with_doi_and_year()
    response = upload(client, data)
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "upload"
    # the four fields an upload must show, like a search result
    assert body["title"] == PAPER_TITLE
    assert body["authors"] == ["Alice Smith", "Bob Jones"]
    assert body["year"] == 2019
    assert body["abstract"].startswith("We study widgets in detail")
    assert body["doi"] == "10.1145/12345.67890"
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
    # the metadata pass reads the first page only, never the whole paper
    [call] = fake_llm.calls
    assert "We study widgets in detail" in call["prompt"]  # page 1
    assert "widgets improve accuracy" not in call["prompt"]  # page 2


@pytest.mark.xfail(
    strict=True,
    reason="SUSPECTED BUG: the footnote-mark filter used on titles (metadata.FOOTNOTE_MARKS) deletes digits "
    "that follow letters, so model names in a heuristic title are mangled: 'YOLOv3: ...' becomes "
    "'YOLOv: ...' and 'ResNet50' loses its digits. Footnote marks are only meant to go from author names.",
)
@pytest.mark.parametrize("title", ["YOLOv3: An Incremental Improvement", "ResNet50 Features for Widget Detection"])
def test_upload_keeps_model_names_in_a_heuristic_title(client, offline_lookups, title):
    body = upload(client, paper_pdf(title=title)).json()  # no LLM, lookups find nothing
    assert body["metadata_source"] == "pdf"
    assert body["title"] == title


def test_upload_survives_a_failing_llm(client, offline_lookups, fake_llm):
    fake_llm.responses = [LLMError("model down")]
    response = upload(client, paper_pdf())
    assert response.status_code == 201
    assert response.json()["title"] == PAPER_TITLE


@pytest.mark.parametrize(
    "filename, expected",
    [("my_great-paper v2.pdf", "my great-paper v2"), ("a__b_c.pdf", "a b c"), ("  .pdf", "Untitled paper")],
)
def test_upload_without_extractable_title_falls_back_to_filename(client, offline_lookups, filename, expected):
    response = upload(client, scanned_like_pdf(), filename=filename)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == expected
    assert body["status"] == "failed"
    assert "OCR" in body["status_detail"]
    assert body["has_pdf"] is True


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


def test_upload_size_cap_is_inclusive(client, offline_lookups, monkeypatch):
    limit = 1024 * 1024
    monkeypatch.setattr(upload_router, "MAX_PDF_BYTES", limit)
    over = upload(client, b"%PDF-" + b"x" * (limit - 4))  # limit + 1 bytes
    assert over.status_code == 413
    assert over.json()["detail"] == "PDFs are limited to 1 MB"
    at_limit = upload(client, b"%PDF-" + b"x" * (limit - 5))  # exactly the limit: passes the size check
    assert at_limit.status_code == 422
    assert at_limit.json()["detail"] == "PDF could not be read"


def test_upload_requires_a_file(client, session):
    assert client.post("/api/papers/upload").status_code == 422
    assert session.query(Paper).count() == 0
    assert list(settings.pdf_dir.iterdir()) == []


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
    session.expire_all()
    assert session.get(Paper, saved.id).status == "downloading"  # the running download is left alone
    assert session.get(Paper, saved.id).pdf_path is None
    assert session.query(Paper).count() == 1


def test_upload_rolls_back_when_the_pdf_cannot_be_stored(session, monkeypatch, tmp_path):
    # "pdfs" is a file, so creating the PDF directory raises an OSError
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "pdfs").write_text("not a directory")
    client = mock_client(lambda request: httpx.Response(404))
    with pytest.raises(OSError):
        pipeline.upload(session, paper_pdf(), "paper.pdf", client)
    assert session.query(Paper).count() == 0
    assert session.query(Chunk).count() == 0


def test_pdf_with_text_but_no_body_sections_is_marked_failed(client, offline_lookups):
    data = build_pdf([[("A Title Page Only", 20, True), ("References", 12, True),
                       ("[1] A. Author. Some cited work. 2020.", 10, False)]])
    body = upload(client, data).json()
    assert body["status"] == "failed"
    assert body["status_detail"] == "The PDF has text, but no body sections were found in it."
    assert client.get(f"/api/papers/{body['id']}/chunks").json() == []


def test_scanned_upload_does_not_call_the_llm(client, offline_lookups, fake_llm):
    fake_llm.responses = [{"title": "Should Not Be Used", "authors": [], "year": 0}]
    body = upload(client, scanned_like_pdf(), filename="scan.pdf").json()
    assert fake_llm.calls == []  # a page without text has nothing for the LLM to read
    assert body["title"] == "scan"


def test_rotated_arxiv_stamp_feeds_metadata_and_the_llm_prompt(client, offline_lookups, fake_llm):
    doc = pymupdf.open(stream=paper_pdf(), filetype="pdf")
    doc[0].insert_text((30, 600), "arXiv:2103.01234v2 [cs.LG] 4 Mar 2021", fontsize=9, rotate=90)
    fake_llm.responses = [{"title": PAPER_TITLE, "authors": ["Alice Smith", "Bob Jones"], "year": 1999}]
    body = upload(client, doc.tobytes()).json()
    assert body["year"] == 2021  # from the stamp's arXiv id; the LLM's year is not trusted over it
    assert body["url"] == "https://arxiv.org/abs/2103.01234"
    assert fake_llm.calls[0]["prompt"].startswith("<page>\narXiv:2103.01234v2")


# ----------------------------------------------------------------------- attach


def attach(client, paper_id, data, **form):
    return client.post(f"/api/papers/{paper_id}/pdf", files={"file": ("p.pdf", data, "application/pdf")}, data=form)


def test_attach_fills_blank_fields_from_the_pdf(client, session):
    paper = add_paper(session, title="My Own Title", authors=[], year=None, abstract=None, doi=None)
    response = attach(client, paper.id, pdf_with_doi_and_year())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["has_pdf"] is True
    assert body["authors"] == ["Alice Smith", "Bob Jones"]
    assert body["year"] == 2019
    assert body["abstract"].startswith("We study widgets")
    assert body["doi"] == "10.1145/12345.67890"


def test_attach_never_overwrites_existing_fields(client, session):
    paper = add_paper(session, title="My Own Title", authors=["Kept Author"], year=2001, abstract="Kept abstract.",
                      doi="10.1/kept", metadata_source="edited")
    body = attach(client, paper.id, pdf_with_doi_and_year()).json()
    assert body["status"] == "ready"
    assert body["title"] == "My Own Title"
    assert body["authors"] == ["Kept Author"]
    assert body["year"] == 2001
    assert body["abstract"] == "Kept abstract."
    assert body["doi"] == "10.1/kept"
    assert body["metadata_source"] == "edited"


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
    paper = add_ready_paper(session)
    before = client.get(f"/api/papers/{paper.id}").json()
    response = attach(client, paper.id, b"not a pdf")
    assert response.status_code == 422
    assert client.get(f"/api/papers/{paper.id}").json() == before
    assert len(client.get(f"/api/papers/{paper.id}/chunks").json()) == 5


def test_attach_while_busy_is_409_and_changes_nothing(client, session):
    paper = add_ready_paper(session, pdf_path="old.pdf")
    paper.status = "processing"
    session.commit()
    (settings.pdf_dir / "old.pdf").write_bytes(b"%PDF-old")
    response = attach(client, paper.id, paper_pdf())
    assert response.status_code == 409
    assert response.json()["detail"] == "The PDF is already being processed"
    session.expire_all()
    stored = session.get(Paper, paper.id)
    assert (stored.status, stored.pdf_path) == ("processing", "old.pdf")
    assert [c.section for c in stored.chunks] == ["Abstract", "Introduction", "Method", "Results", "Conclusion"]
    assert (settings.pdf_dir / "old.pdf").read_bytes() == b"%PDF-old"
    assert not (settings.pdf_dir / f"{paper.id}.pdf").exists()


def test_attach_to_missing_paper_is_404(client, session):
    assert attach(client, 12345, paper_pdf()).status_code == 404
    assert session.query(Paper).count() == 0
    assert list(settings.pdf_dir.iterdir()) == []


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


def test_saving_a_paper_with_pdf_url_downloads_and_processes_it(client, session, download_server):
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
    stored = session.query(Chunk).filter_by(paper_id=paper["id"]).all()
    assert stored and all(c.embedding is not None for c in stored)  # embedded, ready for Q&A


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


def test_paper_deleted_during_download_is_not_recreated(client, session, monkeypatch):
    url = "https://oa.example.org/p.pdf"
    paper = add_paper(session, pdf_url=url)

    def handler(request):
        # the user deletes the paper while its PDF is still downloading
        with SessionLocal() as other:
            other.delete(other.get(Paper, paper.id))
            other.commit()
        return httpx.Response(200, content=paper_pdf())

    monkeypatch.setattr(http, "make_download_client", lambda timeout=30.0: mock_client(handler))
    assert client.post(f"/api/papers/{paper.id}/pdf/fetch").status_code == 202
    session.expire_all()
    assert session.query(Paper).count() == 0
    assert session.query(Chunk).count() == 0
    assert list(settings.pdf_dir.iterdir()) == []


def test_upload_stays_usable_when_embedding_fails(client, session, offline_lookups, fake_embedder, fake_llm):
    fake_embedder.fail = True
    response = upload(client, paper_pdf())
    assert response.status_code == 201
    paper_id = response.json()["id"]
    assert client.get(f"/api/papers/{paper_id}").json()["status"] == "ready"
    assert all(c.embedding is None for c in session.query(Chunk).filter_by(paper_id=paper_id))

    # the first question embeds the chunks itself once the model works again
    fake_embedder.fail = False
    fake_llm.responses = [{"answer": "Yes [1].", "found": True, "background": ""}]
    assert client.post(f"/api/papers/{paper_id}/chat", json={"question": "Results?"}).status_code == 200
    session.expire_all()
    assert all(c.embedding is not None for c in session.query(Chunk).filter_by(paper_id=paper_id))


def test_unexpected_embedding_error_is_contained(session, monkeypatch):
    paper = add_ready_paper(session)

    def explode(*args):
        raise RuntimeError("chunks deleted meanwhile")

    monkeypatch.setattr(pipeline.retrieval, "ensure_embeddings", explode)
    pipeline.embed_paper(paper.id)  # must not raise
    pipeline.embed_paper(999999)  # a paper deleted before its task ran is ignored
    session.expire_all()
    assert session.get(Paper, paper.id).status == "ready"


def test_download_task_ignores_papers_not_queued(session, download_server):
    _, seen = download_server
    paper = add_paper(session, pdf_url="https://oa.example.org/p.pdf", status="no_pdf")
    pipeline.download_pdf(paper.id)
    pipeline.download_pdf(999999)  # deleted paper
    assert seen == []


@pytest.mark.xfail(
    strict=True,
    reason="SUSPECTED BUG (API-level only): POST /pdf/fetch is accepted for a paper that is already processed "
    "and ready, and if that retry fails download_pdf sets status='no_pdf' although the stored PDF and chunks "
    "are untouched, so has_pdf is true but summary/Q&A answer 409 'full text isn't available'. "
    "recover_interrupted() shows the intended rule: a paper with chunks stays 'ready'. The frontend only shows "
    "the 'Download open-access PDF' button when has_pdf is false, so users reach this through the API only.",
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


def test_fetch_pdf_size_cap_is_inclusive(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_PDF_BYTES", 1000)
    at_limit = b"%PDF-" + b"x" * 995
    assert fetch_pdf(mock_client(lambda r: httpx.Response(200, content=at_limit)), "https://e.org/a.pdf") == at_limit
    with pytest.raises(DownloadError, match="larger than"):
        fetch_pdf(mock_client(lambda r: httpx.Response(200, content=at_limit + b"x")), "https://e.org/b.pdf")


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


def test_download_backend_tries_each_public_address_then_gives_up(monkeypatch):
    tried = []

    def connect(self, host, port, *args, **kwargs):
        tried.append(host)
        if host == "93.184.216.35":
            return "stream"
        raise httpcore.ConnectError(f"{host} unreachable")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(["93.184.216.34", "93.184.216.35"]))
    assert http.PublicNetworkBackend().connect_tcp("example.org", 443) == "stream"
    assert tried == ["93.184.216.34", "93.184.216.35"]

    tried.clear()
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver(["93.184.216.34", "93.184.216.36"]))
    with pytest.raises(httpcore.ConnectError, match="93.184.216.36 unreachable"):
        http.PublicNetworkBackend().connect_tcp("example.org", 443)
    assert tried == ["93.184.216.34", "93.184.216.36"]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254"])
def test_download_client_refuses_private_destinations_before_connecting(stubbed_sockets, address):
    stubbed_sockets["dns"]["files.example.org"] = [address]
    with http.make_download_client() as client:
        with pytest.raises(DownloadError, match="private or local network address"):
            fetch_pdf(client, "http://files.example.org/paper.pdf")
    assert stubbed_sockets["connects"] == []


def test_download_client_connects_directly_to_the_checked_ip_ignoring_proxies(stubbed_sockets, monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, "http://proxy.internal:3128")
    stubbed_sockets["dns"]["files.example.org"] = ["93.184.216.34"]
    with http.make_download_client() as client:
        with pytest.raises(DownloadError, match=r"the download failed \(ConnectError\)"):
            fetch_pdf(client, "http://files.example.org/paper.pdf")
    # the validated address itself, not the hostname (a second DNS answer can't swap it) and not the proxy
    assert stubbed_sockets["connects"] == [("93.184.216.34", 80)]


def test_download_of_an_unresolvable_host_is_a_download_error(stubbed_sockets):
    with http.make_download_client() as client:
        with pytest.raises(DownloadError, match=r"the download failed \(ConnectError\)"):
            fetch_pdf(client, "https://no-such-host.example/paper.pdf")
    assert stubbed_sockets["connects"] == []


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
