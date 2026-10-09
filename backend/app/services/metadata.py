"""Bibliographic metadata for uploaded PDFs.

Passes, cheapest first:
1. Heuristics over the PDF's own metadata and its first page (title from the
   largest font, authors from the lines under it, abstract from its section).
2. A lookup that replaces the guesses: Semantic Scholar by DOI, arXiv id or
   title, then arXiv itself by id or title (S2 often rate-limits keyless use).

An identifier lookup is exact. A title search only finds the most similar
title, so its `source` ends in "_title" and the UI asks the user to verify it.

An LLM pass over the first page joins these in M3. All fields stay editable.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date
from difflib import SequenceMatcher

import httpx

from app.services.library import normalize_title
from app.schemas import PaperCreate
from app.services.pdf import ExtractedPdf, Line
from app.services.search import arxiv_terms, query_arxiv

S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/"
S2_FIELDS = "paperId,title,authors,year,abstract,url,externalIds"
TITLE_MATCH_THRESHOLD = 0.9

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20\d\d)\b")

AFFILIATION_WORDS = re.compile(
    r"\b(universit|institut|department|dept\b|laborator|lab\b|school|college|"
    r"center|centre|research|inc\b|corp|ltd|gmbh|academy|hospital|faculty|"
    r"google|microsoft|meta\b|facebook|openai|deepmind|amazon|nvidia|ibm\b|"
    r"equal contribution|correspond|work done|email|preprint|proceedings|"
    r"conference|workshop|journal|arxiv)",
    re.I,
)
NAME_PARTICLES = {"de", "da", "del", "della", "der", "di", "du", "la", "le", "van", "von", "y", "bin", "al"}
FOOTNOTE_MARKS = re.compile(r"[*∗†‡§¶⋆#♠♣♥♦✝✉]|(?<=[^\W\d_])\d+(?:,\d+)*\b")


@dataclass
class ExtractedMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    arxiv_id: str | None = None
    external_id: str | None = None  # S2 paperId or arXiv id after a match
    # pdf | semantic_scholar | semantic_scholar_title | arxiv | arxiv_title
    source: str = "pdf"

    @property
    def external_source(self) -> str:
        """The library `source` that `external_id` belongs to, for duplicate checks."""
        if self.source.startswith("semantic_scholar"):
            return "s2"
        return "arxiv" if self.source.startswith("arxiv") else "upload"

    def as_paper_fields(self) -> dict:
        data = asdict(self)
        data.pop("arxiv_id")
        data["metadata_source"] = data.pop("source")
        return data


def extract_metadata(pdf: ExtractedPdf) -> ExtractedMetadata:
    first_page = [line for line in pdf.lines if line.page == 0]
    page_text = " ".join(line.text for line in first_page)
    stamp_text = f"{pdf.rotated_text} {page_text}"

    title_lines = _title_lines(first_page, pdf.body_size)
    title = _clean_title(" ".join(l.text for l in title_lines)) if title_lines else None
    meta_title = _clean_title(pdf.pdf_metadata.get("title") or "")
    # Trust the embedded title only when the first page confirms it; tools often
    # leave "Microsoft Word - draft3.docx" or similar there.
    if meta_title and (not title or normalize_title(meta_title) in normalize_title(page_text)):
        title = meta_title

    authors = _authors_after(first_page, title_lines) if title_lines else []
    if not authors:
        authors = _split_names(pdf.pdf_metadata.get("author") or "")

    abstract = next((s.text for s in pdf.sections if s.title == "Abstract"), None)
    arxiv = ARXIV_RE.search(stamp_text)
    doi = DOI_RE.search(stamp_text)

    return ExtractedMetadata(
        title=title,
        authors=authors,
        year=_year(arxiv.group(1) if arxiv else None, page_text, pdf.pdf_metadata),
        abstract=" ".join(abstract.split())[:5000] if abstract else None,
        doi=doi.group(1).rstrip(".,;)]") if doi else None,
        arxiv_id=arxiv.group(1) if arxiv else None,
        url=f"https://arxiv.org/abs/{arxiv.group(1)}" if arxiv else None,
    )


def _title_lines(lines: list[Line], body_size: float) -> list[Line]:
    """The run of lines in the largest font on the top half of page one."""
    top = [l for l in lines if l.y0 < l.page_height * 0.5 and len(l.text) > 1]
    if not top:
        return []
    biggest = max(l.size for l in top)
    if biggest < body_size + 1:
        return []  # no distinct title font; leave it to the other sources
    start = next(i for i, l in enumerate(top) if l.size >= biggest - 0.5)
    run = []
    for line in top[start:]:
        if line.size < biggest - 0.5:
            break
        run.append(line)
    return run


def _clean_title(text: str) -> str | None:
    text = FOOTNOTE_MARKS.sub("", text)
    text = " ".join(text.split()).strip(" .,;:")
    if not 8 <= len(text) <= 300:
        return None
    if re.search(r"\.(pdf|docx?|tex|dvi)$|^microsoft word|^untitled", text, re.I):
        return None
    return text


def _authors_after(lines: list[Line], title_lines: list[Line]) -> list[str]:
    start = lines.index(title_lines[-1]) + 1
    names: list[str] = []
    for line in lines[start:start + 25]:
        if re.match(r"^abstract\b", line.text, re.I) or line.y0 > line.page_height * 0.6:
            break
        if "@" in line.text or AFFILIATION_WORDS.search(line.text):
            continue
        for name in _split_names(line.text):
            if name not in names:
                names.append(name)
    return names[:50]


def _split_names(text: str) -> list[str]:
    text = FOOTNOTE_MARKS.sub(" ", text)
    parts = re.split(r",|;|\band\b|&|\s{2,}|·", text)
    return [name for p in parts if (name := " ".join(p.split())) and _looks_like_name(name)]


def _looks_like_name(name: str) -> bool:
    words = name.split()
    if not 2 <= len(words) <= 5 or len(name) > 50:
        return False
    for word in words:
        if word.lower() in NAME_PARTICLES:
            continue
        if not re.fullmatch(r"[^\W\d_][\w'’.\-]*", word) or not word[0].isupper():
            return False
    return True


def _year(arxiv_id: str | None, page_text: str, pdf_metadata: dict) -> int | None:
    this_year = date.today().year
    if arxiv_id:
        return 2000 + int(arxiv_id[:2])
    years = [int(y) for y in YEAR_RE.findall(page_text) if int(y) <= this_year + 1]
    if years:
        return max(years)
    created = re.match(r"D:(\d{4})", pdf_metadata.get("creationDate") or "")
    return int(created.group(1)) if created else None


def lookup(client: httpx.Client, meta: ExtractedMetadata, s2_api_key: str | None = None) -> ExtractedMetadata:
    """Improve `meta` from Semantic Scholar, else arXiv; unchanged if neither matches."""
    found = lookup_semantic_scholar(client, meta, s2_api_key)
    return found if found is not meta else lookup_arxiv(client, meta)


def lookup_semantic_scholar(
    client: httpx.Client, meta: ExtractedMetadata, api_key: str | None = None
) -> ExtractedMetadata:
    """Return `meta` improved by a confident Semantic Scholar match, or unchanged."""
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"fields": S2_FIELDS}
    try:
        for key in (f"DOI:{meta.doi}" if meta.doi else None,
                    f"arXiv:{meta.arxiv_id}" if meta.arxiv_id else None):
            if key:
                resp = client.get(S2_PAPER_URL + key, params=params, headers=headers)
                if resp.status_code == 200:
                    return _merge(meta, resp.json(), "semantic_scholar")
                if resp.status_code != 404:
                    resp.raise_for_status()
        if meta.title:
            resp = client.get(
                S2_PAPER_URL + "search/match",
                params={**params, "query": meta.title},
                headers=headers,
            )
            if resp.status_code == 200:
                match = (resp.json().get("data") or [None])[0]
                if match and _similar_titles(meta.title, match.get("title") or ""):
                    return _merge(meta, match, "semantic_scholar_title")
    except (httpx.HTTPError, ValueError):
        pass  # rate-limited or offline: the heuristic metadata stands
    return meta


def lookup_arxiv(client: httpx.Client, meta: ExtractedMetadata) -> ExtractedMetadata:
    """Return `meta` improved by its arXiv record (by id, else by title), or unchanged."""
    try:
        if meta.arxiv_id:
            for paper in query_arxiv(client, {"id_list": meta.arxiv_id, "max_results": 1}):
                return _merge_arxiv(meta, paper, "arxiv")
        if meta.title:
            words = normalize_title(meta.title).split()
            # The exact phrase ranks the paper first; plain terms are the fallback.
            for query in (f'ti:"{" ".join(words)}"',
                          " AND ".join(f"ti:{w}" for w in arxiv_terms(" ".join(words)))):
                candidates = query_arxiv(client, {"search_query": query, "max_results": 10})
                best = max(candidates, key=lambda p: _title_ratio(meta.title, p.title), default=None)
                if best and _similar_titles(meta.title, best.title):
                    return _merge_arxiv(meta, best, "arxiv_title")
    except (httpx.HTTPError, ET.ParseError):
        pass
    return meta


def _title_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _similar_titles(a: str, b: str) -> bool:
    return _title_ratio(a, b) >= TITLE_MATCH_THRESHOLD


def _merge_arxiv(meta: ExtractedMetadata, paper: PaperCreate, source: str) -> ExtractedMetadata:
    return ExtractedMetadata(
        title=paper.title,
        authors=paper.authors or meta.authors,
        year=paper.year or meta.year,
        abstract=paper.abstract or meta.abstract,
        doi=paper.doi or meta.doi,
        url=paper.url or meta.url,
        arxiv_id=paper.external_id,
        external_id=paper.external_id,
        source=source,
    )


def _merge(meta: ExtractedMetadata, item: dict, source: str) -> ExtractedMetadata:
    ids = item.get("externalIds") or {}
    authors = [a["name"] for a in item.get("authors") or [] if a.get("name")]
    return ExtractedMetadata(
        title=" ".join((item.get("title") or "").split()) or meta.title,
        authors=authors or meta.authors,
        year=item.get("year") or meta.year,
        abstract=" ".join((item.get("abstract") or "").split()) or meta.abstract,
        doi=ids.get("DOI") or meta.doi,
        url=item.get("url") or meta.url,
        arxiv_id=ids.get("ArXiv") or meta.arxiv_id,
        external_id=item.get("paperId"),
        source=source,
    )
