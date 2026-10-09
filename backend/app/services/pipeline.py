"""Getting a PDF into a paper: download or upload → store → extract → chunk.

A paper's `status` tracks this: no_pdf → downloading → processing → ready, or
`failed` when the PDF has no usable text. `status_detail` says why in words a
user can act on.
"""

import logging
import re
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Chunk, Paper
from app.services import http
from app.services.library import DuplicateIndex, normalize_doi
from app.services.metadata import ExtractedMetadata, extract_metadata, lookup_semantic_scholar
from app.services.pdf import MAX_PDF_BYTES, ExtractedPdf, PdfError, extract

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 30.0
BUSY = ("downloading", "processing")
HAS_PDF = ("ready", *BUSY)
UPLOAD_HINT = "Upload the PDF to enable AI features."


class DuplicatePdf(Exception):
    """The upload matches a library paper that already has a PDF."""

    def __init__(self, paper_id: int):
        super().__init__(paper_id)
        self.paper_id = paper_id


class DownloadError(Exception):
    pass


def pdf_file(paper: Paper) -> Path | None:
    return settings.pdf_dir / paper.pdf_path if paper.pdf_path else None


def remove_pdf(paper: Paper) -> None:
    if (path := pdf_file(paper)) is not None:
        path.unlink(missing_ok=True)


def ingest(session: Session, paper: Paper, data: bytes, extracted: ExtractedPdf | None = None) -> None:
    """Store `data` as `paper`'s PDF and replace its chunks. Raises PdfError if unreadable."""
    extracted = extracted or extract(data)
    if paper.id is None:
        session.flush()  # the file is named after the id
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    name = f"{paper.id}.pdf"
    (settings.pdf_dir / name).write_bytes(data)

    paper.pdf_path = name
    paper.page_count = extracted.page_count
    paper.chunks = [
        Chunk(
            ordinal=i,
            section=c.section,
            text=c.text,
            page_start=c.page_start,
            page_end=c.page_end,
            n_tokens=c.n_tokens,
        )
        for i, c in enumerate(extracted.chunks)
    ]
    if paper.chunks:
        paper.status, paper.status_detail = "ready", None
    elif not extracted.has_text:
        paper.status = "failed"
        paper.status_detail = (
            "No text could be extracted. The PDF may be scanned images, and OCR isn't supported."
        )
    else:
        paper.status = "failed"
        paper.status_detail = "The PDF has text, but no body sections were found in it."
    session.commit()


def fill_missing(paper: Paper, meta: ExtractedMetadata) -> None:
    """Fill blank fields of a saved paper from a PDF, without overwriting anything."""
    paper.abstract = paper.abstract or meta.abstract
    paper.year = paper.year or meta.year
    paper.authors = paper.authors or meta.authors
    paper.doi = paper.doi or normalize_doi(meta.doi)


def upload(
    session: Session, data: bytes, filename: str | None, client: httpx.Client
) -> tuple[Paper, bool]:
    """Turn an uploaded PDF into a library paper. Returns (paper, created).

    If the PDF is a paper already saved without a PDF, it is attached to that
    paper instead of creating a duplicate.
    """
    extracted = extract(data)
    meta = lookup_semantic_scholar(client, extract_metadata(extracted), settings.semantic_scholar_api_key)

    fields = meta.as_paper_fields()
    fields["title"] = fields["title"] or _title_from_filename(filename)
    fields["doi"] = normalize_doi(fields["doi"])
    paper = Paper(source="upload", **fields)

    # A Semantic Scholar match carries an S2 paperId, which identifies saved s2 papers.
    probe = Paper(**{**fields, "source": "s2" if meta.external_id else "upload"})
    if (existing_id := DuplicateIndex(session).match(probe)) is not None:
        existing = session.get(Paper, existing_id)
        if existing.status in HAS_PDF:
            raise DuplicatePdf(existing.id)
        fill_missing(existing, meta)
        ingest(session, existing, data, extracted)
        return existing, False

    session.add(paper)
    try:
        ingest(session, paper, data, extracted)
    except OSError:
        session.rollback()
        raise
    return paper, True


def attach(session: Session, paper: Paper, data: bytes) -> None:
    """Attach an uploaded PDF to an existing paper, replacing any earlier one."""
    extracted = extract(data)
    fill_missing(paper, extract_metadata(extracted))
    ingest(session, paper, data, extracted)


def _title_from_filename(filename: str | None) -> str:
    stem = Path(filename or "").stem
    return " ".join(re.split(r"[_\s]+", stem)).strip() or "Untitled paper"


def queue_download(session: Session, paper: Paper) -> bool:
    """Mark `paper` for an open-access download. The caller runs `download_pdf`."""
    if not paper.pdf_url or paper.status in BUSY:
        return False
    paper.status, paper.status_detail = "downloading", None
    session.commit()
    return True


def download_pdf(paper_id: int) -> None:
    """Background task: fetch a paper's open-access PDF and process it."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None or paper.status != "downloading":
            return
        try:
            with http.make_client(timeout=DOWNLOAD_TIMEOUT) as client:
                data = fetch_pdf(client, paper.pdf_url)
            if session.get(Paper, paper_id, populate_existing=True) is None:
                return  # deleted while downloading
            paper.status = "processing"
            session.commit()
            ingest(session, paper, data)
        except (DownloadError, PdfError) as exc:
            paper.status = "no_pdf"
            paper.status_detail = f"The open-access PDF couldn't be used: {exc}. {UPLOAD_HINT}"
            session.commit()
        except Exception:
            log.exception("PDF pipeline failed for paper %s", paper_id)
            session.rollback()
            if (paper := session.get(Paper, paper_id)) is not None:
                paper.status = "failed"
                paper.status_detail = f"Processing the PDF failed unexpectedly. {UPLOAD_HINT}"
                session.commit()


def fetch_pdf(client: httpx.Client, url: str) -> bytes:
    if not re.match(r"^https?://", url or "", re.I):
        raise DownloadError("the link is not an http(s) URL")
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            data = bytearray()
            for part in resp.iter_bytes():
                data += part
                if len(data) > MAX_PDF_BYTES:
                    raise DownloadError("the file is larger than 50 MB")
    except httpx.HTTPStatusError as exc:
        raise DownloadError(f"the server answered HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise DownloadError(f"the download failed ({type(exc).__name__})") from exc
    if b"%PDF-" not in data[:1024]:
        raise DownloadError("the link leads to a web page, not a PDF file")
    return bytes(data)


def recover_interrupted(session: Session) -> None:
    """Papers left mid-pipeline by a server stop would otherwise spin forever."""
    for paper in session.scalars(select(Paper).where(Paper.status.in_(BUSY))):
        paper.status = "ready" if paper.chunks else "no_pdf"
        paper.status_detail = (
            None if paper.chunks else "A server restart interrupted the download. Retry it, or upload the PDF."
        )
    session.commit()
