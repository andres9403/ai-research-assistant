from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import SearchResponse, SearchResult
from app.services.library import DuplicateIndex
from app.services.search import SearchError, SearchService, get_search_service

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(20, ge=1, le=50),
    service: SearchService = Depends(get_search_service),
    session: Session = Depends(get_session),
) -> SearchResponse:
    if not q.strip():
        raise HTTPException(422, "Query must not be blank")
    try:
        outcome = service.search(q.strip(), limit)
    except SearchError as exc:
        raise HTTPException(502, str(exc)) from exc

    index = DuplicateIndex(session)
    return SearchResponse(
        provider=outcome.provider,
        fallback_reason=outcome.fallback_reason,
        results=[
            SearchResult(**paper.model_dump(), saved_id=index.match(paper))
            for paper in outcome.results
        ],
    )
