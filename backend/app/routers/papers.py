from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Chunk, Paper
from app.schemas import ChunkOut, PaperCreate, PaperOut, PaperUpdate
from app.services import library, pipeline

router = APIRouter(prefix="/api/papers", tags=["papers"])


def get_paper_or_404(paper_id: int, session: Session = Depends(get_session)) -> Paper:
    if (paper := session.get(Paper, paper_id)) is None:
        raise HTTPException(404, "Paper not found")
    return paper


@router.get("", response_model=list[PaperOut])
def list_papers(
    q: str | None = Query(None, max_length=300),
    source: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    session: Session = Depends(get_session),
) -> list[Paper]:
    return library.list_papers(session, q, source, year_from, year_to)


@router.post("", response_model=PaperOut, status_code=201)
def save_paper(
    data: PaperCreate, background: BackgroundTasks, session: Session = Depends(get_session)
) -> Paper:
    paper, created = library.save_paper(session, data)
    if not created:
        raise HTTPException(
            409, {"message": "This paper is already in your library", "paper_id": paper.id}
        )
    if pipeline.queue_download(session, paper):
        background.add_task(pipeline.download_pdf, paper.id)
    return paper


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(paper: Paper = Depends(get_paper_or_404)) -> Paper:
    return paper


@router.patch("/{paper_id}", response_model=PaperOut)
def update_paper(
    data: PaperUpdate,
    paper: Paper = Depends(get_paper_or_404),
    session: Session = Depends(get_session),
) -> Paper:
    try:
        library.update_paper(session, paper, data)
    except library.DuplicatePaper as exc:
        raise HTTPException(
            409, {"message": "Another library paper has this title or DOI", "paper_id": exc.paper_id}
        ) from exc
    return paper


@router.delete("/{paper_id}", status_code=204)
def delete_paper(
    paper: Paper = Depends(get_paper_or_404), session: Session = Depends(get_session)
) -> Response:
    pipeline.remove_pdf(paper)
    session.delete(paper)
    session.commit()
    return Response(status_code=204)


@router.get("/{paper_id}/pdf")
def get_pdf(paper: Paper = Depends(get_paper_or_404)) -> FileResponse:
    path = pipeline.pdf_file(paper)
    if path is None or not path.is_file():
        raise HTTPException(404, "This paper has no PDF")
    return FileResponse(path, media_type="application/pdf", content_disposition_type="inline")


@router.post("/{paper_id}/pdf/fetch", response_model=PaperOut, status_code=202)
def fetch_pdf(
    background: BackgroundTasks,
    paper: Paper = Depends(get_paper_or_404),
    session: Session = Depends(get_session),
) -> Paper:
    """Retry the open-access download, e.g. for papers saved before M2."""
    if not paper.pdf_url:
        raise HTTPException(409, "This paper has no open-access PDF link")
    if not pipeline.queue_download(session, paper):
        raise HTTPException(409, "The PDF is already being processed")
    background.add_task(pipeline.download_pdf, paper.id)
    return paper


@router.get("/{paper_id}/chunks", response_model=list[ChunkOut])
def list_chunks(paper: Paper = Depends(get_paper_or_404)) -> list[Chunk]:
    return paper.chunks
