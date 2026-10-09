from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Paper, Summary
from app.routers.papers import get_paper_or_404
from app.schemas import SummaryOut
from app.services import llm, summaries
from app.services.pipeline import UPLOAD_HINT

router = APIRouter(prefix="/api/papers", tags=["ai"])


def resolve_llm(name: str | None) -> llm.LLMProvider:
    try:
        return llm.resolve(name)
    except llm.LLMNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{paper_id}/summary", response_model=SummaryOut)
def get_summary(paper: Paper = Depends(get_paper_or_404)) -> Summary:
    if paper.summary is None:
        raise HTTPException(404, "This paper has no summary yet")
    return paper.summary


@router.post("/{paper_id}/summary", response_model=SummaryOut)
def create_summary(
    provider: str | None = Query(None, description="anthropic | openai; defaults to LLM_PROVIDER"),
    refresh: bool = Query(False, description="Regenerate even if a summary is cached"),
    paper: Paper = Depends(get_paper_or_404),
    session: Session = Depends(get_session),
) -> Summary:
    """Return the cached summary, or generate one from the paper's text."""
    if paper.summary is not None and not refresh:
        return paper.summary
    model = resolve_llm(provider)
    if paper.status != "ready" or not paper.chunks:
        raise HTTPException(409, f"This paper's full text isn't available. {UPLOAD_HINT}")
    try:
        return summaries.generate(session, paper, model)
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
