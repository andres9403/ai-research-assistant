from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # s2 | arxiv | upload
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    doi: Mapped[str | None] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    abstract: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    # no_pdf | downloading | processing | ready | failed
    status: Mapped[str] = mapped_column(String(16), default="no_pdf")
    status_detail: Mapped[str | None] = mapped_column(Text)  # why a PDF is missing or failed
    page_count: Mapped[int | None] = mapped_column(Integer)
    metadata_source: Mapped[str | None] = mapped_column(String(32))  # pdf | semantic_scholar
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="Chunk.ordinal"
    )
    summary: Mapped["Summary | None"] = relationship(
        back_populates="paper", cascade="all, delete-orphan", uselist=False
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )

    @property
    def has_pdf(self) -> bool:
        return self.pdf_path is not None


class Chunk(Base):
    """A paragraph-aligned slice of a paper's cleaned text, the unit of retrieval."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    n_tokens: Mapped[int] = mapped_column(Integer)
    # float32 vector from the local embedding model; null until embedded
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)

    paper: Mapped[Paper] = relationship(back_populates="chunks")


class Summary(Base):
    """The cached structured summary of a paper; one per paper, replaced on regenerate."""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSON)  # tldr, problem, approach, data, results, limitations
    sections: Mapped[list[dict]] = mapped_column(JSON)  # what was sent: [{section, page_start, page_end}]
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="summary")


class ChatMessage(Base):
    """One turn of a paper's Q&A thread. Assistant turns carry the passages they cite."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # [{n, chunk_id, section, page_start, page_end, text}]; a snapshot, so it outlives the chunk
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    provider: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="messages")
