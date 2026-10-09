from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaperBase(BaseModel):
    external_id: str | None = None
    doi: str | None = None
    title: str = Field(min_length=1)
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    url: str | None = None
    pdf_url: str | None = None


class PaperCreate(PaperBase):
    """A search result as the client sends it back to be saved."""

    source: Literal["s2", "arxiv"]


class SearchResult(PaperCreate):
    saved_id: int | None = None  # library id when this paper is already saved


class SearchResponse(BaseModel):
    provider: Literal["s2", "arxiv"]
    fallback_reason: str | None = None  # why Semantic Scholar wasn't used
    results: list[SearchResult]


class PaperOut(PaperBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    status: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops the timezone on read; every timestamp we store is UTC.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
