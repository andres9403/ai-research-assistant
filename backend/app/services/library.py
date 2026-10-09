"""The saved-paper library: saving with duplicate detection, listing, deleting."""

import re
import unicodedata

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.models import Paper
from app.schemas import PaperCreate, PaperUpdate


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


class DuplicatePaper(Exception):
    def __init__(self, paper_id: int):
        super().__init__(paper_id)
        self.paper_id = paper_id


class DuplicateIndex:
    """Matches papers against the library by DOI, then source id, then normalised title.

    `match` accepts anything with source, external_id, doi and title attributes.
    """

    def __init__(self, session: Session, exclude_id: int | None = None):
        self.by_doi: dict[str, int] = {}
        self.by_external: dict[tuple[str, str], int] = {}
        self.by_title: dict[str, int] = {}
        stmt = select(Paper.id, Paper.source, Paper.external_id, Paper.doi, Paper.title)
        if exclude_id is not None:
            stmt = stmt.where(Paper.id != exclude_id)
        for pid, source, external_id, doi, title in session.execute(stmt):
            if doi := normalize_doi(doi):
                self.by_doi.setdefault(doi, pid)
            if external_id:
                self.by_external.setdefault((source, external_id), pid)
            if key := normalize_title(title):
                self.by_title.setdefault(key, pid)

    def match(self, paper: PaperCreate | Paper) -> int | None:
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


def update_paper(session: Session, paper: Paper, data: PaperUpdate) -> None:
    """Apply user edits. Raises DuplicatePaper if they'd make it match another paper."""
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes and not (changes["title"] or "").strip():
        changes.pop("title")  # a paper always keeps a title
    if "authors" in changes:
        changes["authors"] = [a.strip() for a in changes["authors"] or [] if a.strip()]
    if "doi" in changes:
        changes["doi"] = normalize_doi(changes["doi"])
    for field in ("title", "abstract", "url"):
        if isinstance(changes.get(field), str):
            changes[field] = changes[field].strip() or None

    for field, value in changes.items():
        setattr(paper, field, value)
    if (other := DuplicateIndex(session, exclude_id=paper.id).match(paper)) is not None:
        session.rollback()
        raise DuplicatePaper(other)
    if changes:
        paper.metadata_source = "edited"
    session.commit()


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
