from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Paper
from app.schemas import PaperCreate, PaperOut
from app.services import library

router = APIRouter(prefix="/api/papers", tags=["papers"])


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
def save_paper(data: PaperCreate, session: Session = Depends(get_session)) -> Paper:
    paper, created = library.save_paper(session, data)
    if not created:
        raise HTTPException(
            409, {"message": "This paper is already in your library", "paper_id": paper.id}
        )
    return paper


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: int, session: Session = Depends(get_session)) -> Paper:
    if (paper := session.get(Paper, paper_id)) is None:
        raise HTTPException(404, "Paper not found")
    return paper


@router.delete("/{paper_id}", status_code=204)
def delete_paper(paper_id: int, session: Session = Depends(get_session)) -> Response:
    if (paper := session.get(Paper, paper_id)) is None:
        raise HTTPException(404, "Paper not found")
    session.delete(paper)
    session.commit()
    return Response(status_code=204)
