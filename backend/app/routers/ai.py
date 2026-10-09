from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ChatMessage, Paper, Summary
from app.routers.papers import get_paper_or_404
from app.schemas import ChatExchangeOut, ChatMessageOut, ChatQuestion, SummaryOut
from app.services import chat, embeddings, llm, summaries
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


@router.get("/{paper_id}/chat", response_model=list[ChatMessageOut])
def get_chat(paper: Paper = Depends(get_paper_or_404)) -> list[ChatMessage]:
    return paper.messages


@router.post("/{paper_id}/chat", response_model=ChatExchangeOut)
def ask_question(
    data: ChatQuestion,
    provider: str | None = Query(None, description="anthropic | openai; defaults to LLM_PROVIDER"),
    paper: Paper = Depends(get_paper_or_404),
    session: Session = Depends(get_session),
) -> ChatExchangeOut:
    """Answer a question from the paper's most relevant passages and save the exchange."""
    model = resolve_llm(provider)
    if paper.status != "ready" or not paper.chunks:
        raise HTTPException(409, f"This paper's full text isn't available. {UPLOAD_HINT}")
    try:
        asked, answered = chat.ask(session, paper, data.question, model)
    except embeddings.EmbeddingError as exc:
        raise HTTPException(503, str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ChatExchangeOut(
        question=ChatMessageOut.model_validate(asked), answer=ChatMessageOut.model_validate(answered)
    )


@router.delete("/{paper_id}/chat", status_code=204)
def clear_chat(paper: Paper = Depends(get_paper_or_404), session: Session = Depends(get_session)) -> Response:
    paper.messages = []
    session.commit()
    return Response(status_code=204)
