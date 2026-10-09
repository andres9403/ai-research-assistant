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


class LLMProviderOut(BaseModel):
    name: str
    label: str
    model: str


class LLMSettingsOut(BaseModel):
    """Only providers with an API key are listed; `default` is None when there are none."""

    default: str | None
    providers: list[LLMProviderOut]


class SummaryContent(BaseModel):
    tldr: str
    problem: str
    approach: str
    data: str
    results: str
    limitations: str


class SummarySection(BaseModel):
    section: str
    page_start: int
    page_end: int


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    paper_id: int
    provider: str
    model: str
    content: SummaryContent
    sections: list[SummarySection]  # the parts of the paper the summary was written from
    input_tokens: int
    output_tokens: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question is empty")
        return value


class Citation(BaseModel):
    n: int  # the [n] marker in the answer
    chunk_id: int
    section: str
    page_start: int
    page_end: int
    text: str  # the cited passage, as it was when the answer was written


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] = []
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ChatExchangeOut(BaseModel):
    question: ChatMessageOut
    answer: ChatMessageOut
