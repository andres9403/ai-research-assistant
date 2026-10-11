from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import PublicationType, SearchFilters, SearchResponse, SearchSort
from app.services.library import DuplicateIndex
from app.services.search import SearchError, SearchService, get_search_service

router = APIRouter(prefix="/api", tags=["search"])


def search_filters(
    author: str | None = Query(None, max_length=100),
    year_from: int | None = Query(None),
    year_to: int | None = Query(None),
    venue: str | None = Query(None, max_length=200),
    publication_type: PublicationType | None = Query(None),
    open_access: bool = Query(False),
    sort: SearchSort = Query("relevance"),
) -> SearchFilters:
    """Read the optional filters from the query string; omitting them all searches by keyword only."""
    try:
        return SearchFilters(
            author=author,
            year_from=year_from,
            year_to=year_to,
            venue=venue,
            publication_type=publication_type,
            open_access=open_access,
            sort=sort,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_context=False)) from exc


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=1, max_length=300),
    limit: int = Query(20, ge=1, le=50),
    filters: SearchFilters = Depends(search_filters),
    service: SearchService = Depends(get_search_service),
    session: Session = Depends(get_session),
) -> SearchResponse:
    if not q.strip():
        raise HTTPException(422, "Query must not be blank")
    try:
        outcome = service.search(q.strip(), limit, filters)
    except SearchError as exc:
        raise HTTPException(502, str(exc)) from exc

    index = DuplicateIndex(session)
    return SearchResponse(
        provider=outcome.provider,
        fallback_reason=outcome.fallback_reason,
        unapplied_filters=outcome.unapplied_filters,
        notes=outcome.notes,
        suggest_s2_key=outcome.suggest_s2_key,
        results=[paper.model_copy(update={"saved_id": index.match(paper)}) for paper in outcome.results],
    )
