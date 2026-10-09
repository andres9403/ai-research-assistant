import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Paper
from app.routers.papers import get_paper_or_404
from app.schemas import PaperOut
from app.services import llm, pipeline
from app.services.http import get_http_client
from app.services.pdf import MAX_PDF_BYTES, PdfError

router = APIRouter(prefix="/api/papers", tags=["upload"])


def read_pdf_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDFs are limited to {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "The uploaded file is empty")
    return data


def metadata_llm(provider: str | None) -> llm.LLMProvider | None:
    """The LLM for the first-page metadata pass, or None to rely on heuristics alone."""
    try:
        return llm.resolve(provider)
    except llm.LLMNotConfigured:
        return None


@router.post("/upload", response_model=PaperOut, status_code=201)
def upload_pdf(
    response: Response,
    file: UploadFile = File(...),
    provider: str | None = Form(None),
    session: Session = Depends(get_session),
    client: httpx.Client = Depends(get_http_client),
) -> Paper:
    """Create a library paper from a PDF, extracting its metadata.

    `provider` picks the LLM for the first-page metadata pass; without a
    configured LLM the upload still works on heuristics and lookups alone.
    Answers 200 instead of 201 when the PDF was attached to a matching paper
    that was already saved without one.
    """
    data = read_pdf_upload(file)
    try:
        paper, created = pipeline.upload(session, data, file.filename, client, metadata_llm(provider))
    except PdfError as exc:
        raise HTTPException(422, str(exc)) from exc
    except pipeline.DuplicatePdf as exc:
        raise HTTPException(
            409, {"message": "This paper is already in your library with a PDF", "paper_id": exc.paper_id}
        ) from exc
    if not created:
        response.status_code = 200
    return paper


@router.post("/{paper_id}/pdf", response_model=PaperOut)
def attach_pdf(
    file: UploadFile = File(...),
    provider: str | None = Form(None),
    paper: Paper = Depends(get_paper_or_404),
    session: Session = Depends(get_session),
) -> Paper:
    """Attach (or replace) the PDF of a saved paper, e.g. one with no open-access copy.

    `provider` picks the LLM that reads page one to fill the paper's blank
    fields, as on upload.
    """
    if paper.status in pipeline.BUSY:
        raise HTTPException(409, "The PDF is already being processed")
    data = read_pdf_upload(file)
    try:
        pipeline.attach(session, paper, data, metadata_llm(provider))
    except PdfError as exc:
        raise HTTPException(422, str(exc)) from exc
    return paper
