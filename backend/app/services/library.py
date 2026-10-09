"""The saved-paper library: saving with duplicate detection, listing, deleting."""

import re
import unicodedata

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.models import Paper
from app.schemas import PaperCreate


def normalize_title(title: str) -> str:
    """Lowercase words without accents, so punctuation and diacritics don't hide a duplicate."""
    decomposed = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[^\W_]+", text))


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    return doi.lower() or None


class DuplicateIndex:
    """Matches papers against the library by DOI, then source id, then normalised title."""

    def __init__(self, session: Session):
        self.by_doi: dict[str, int] = {}
        self.by_external: dict[tuple[str, str], int] = {}
        self.by_title: dict[str, int] = {}
        rows = session.execute(
            select(Paper.id, Paper.source, Paper.external_id, Paper.doi, Paper.title)
        )
        for pid, source, external_id, doi, title in rows:
            if doi := normalize_doi(doi):
                self.by_doi.setdefault(doi, pid)
            if external_id:
                self.by_external.setdefault((source, external_id), pid)
            if key := normalize_title(title):
                self.by_title.setdefault(key, pid)

    def match(self, paper: PaperCreate) -> int | None:
        if (doi := normalize_doi(paper.doi)) and doi in self.by_doi:
            return self.by_doi[doi]
        if paper.external_id and (paper.source, paper.external_id) in self.by_external:
            return self.by_external[(paper.source, paper.external_id)]
        return self.by_title.get(normalize_title(paper.title))


def save_paper(session: Session, data: PaperCreate) -> tuple[Paper, bool]:
    """Insert `data` unless it duplicates a saved paper. Returns (paper, created)."""
    if (existing_id := DuplicateIndex(session).match(data)) is not None:
        return session.get(Paper, existing_id), False

    paper = Paper(**data.model_dump())
    paper.doi = normalize_doi(data.doi)
    session.add(paper)
    session.commit()
    return paper, True


def list_papers(
    session: Session,
    q: str | None = None,
    source: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[Paper]:
    stmt = select(Paper).order_by(Paper.created_at.desc(), Paper.id.desc())
    if q := (q or "").strip():
        stmt = stmt.where(
            or_(
                Paper.title.icontains(q, autoescape=True),
                Paper.abstract.icontains(q, autoescape=True),
                cast(Paper.authors, String).icontains(q, autoescape=True),
            )
        )
    if source:
        stmt = stmt.where(Paper.source == source)
    if year_from is not None:
        stmt = stmt.where(Paper.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Paper.year <= year_to)
    return list(session.scalars(stmt))
