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


class PaperUpdate(BaseModel):
    """Editable metadata. Omitted fields are left unchanged."""

    title: str | None = Field(None, min_length=1)
    authors: list[str] | None = None
    year: int | None = Field(None, ge=1000, le=2100)
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None


class PaperOut(PaperBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    status: str
    status_detail: str | None = None
    has_pdf: bool = False
    page_count: int | None = None
    metadata_source: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops the timezone on read; every timestamp we store is UTC.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ordinal: int
    section: str
    page_start: int
    page_end: int
    n_tokens: int
    text: str
