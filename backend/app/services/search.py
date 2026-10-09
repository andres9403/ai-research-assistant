"""Paper search: Semantic Scholar first, arXiv as fallback.

Results are normalised to `PaperCreate` so the client can send one straight
back to `POST /api/papers`. Nothing here touches the database.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from app.config import settings
from app.services import http
from app.schemas import PaperCreate

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "paperId,title,authors,year,abstract,url,externalIds,openAccessPdf"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"

# arXiv's index drops these, so requiring them makes a query match nothing.
ARXIV_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "to", "was", "with", "all", "we", "you", "our",
}

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class SearchError(Exception):
    """Every search provider failed."""


@dataclass
class SearchOutcome:
    provider: str
    results: list[PaperCreate]
    fallback_reason: str | None = None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return " ".join(text.split()) or None


class SearchService:
    def __init__(self, client: httpx.Client, s2_api_key: str | None = None):
        self.client = client
        self.s2_api_key = s2_api_key

    def search(self, query: str, limit: int = 20) -> SearchOutcome:
        try:
            results = self.search_semantic_scholar(query, limit)
            if results:
                return SearchOutcome("s2", results)
            reason = "Semantic Scholar returned no results"
        except (httpx.HTTPError, ValueError) as exc:
            reason = f"Semantic Scholar unavailable ({_describe(exc)})"

        try:
            return SearchOutcome("arxiv", self.search_arxiv(query, limit), reason)
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise SearchError(f"{reason}; arXiv unavailable ({_describe(exc)})") from exc

    def search_semantic_scholar(self, query: str, limit: int) -> list[PaperCreate]:
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}
        resp = self.client.get(
            S2_SEARCH_URL,
            params={"query": query, "limit": limit, "fields": S2_FIELDS},
            headers=headers,
        )
        resp.raise_for_status()
        return [p for item in resp.json().get("data") or [] if (p := _parse_s2(item))]

    def search_arxiv(self, query: str, limit: int) -> list[PaperCreate]:
        # AND the words together; arXiv's default OR ranks loosely related papers first.
        words = arxiv_terms(query)
        if not words:
            return []
        query = " AND ".join(f"all:{w}" for w in words)
        return query_arxiv(self.client, {"search_query": query, "max_results": limit})


def arxiv_terms(text: str) -> list[str]:
    words = re.findall(r"[\w-]+", text)
    return [w for w in words if w.lower() not in ARXIV_STOPWORDS] or words


def query_arxiv(client: httpx.Client, params: dict) -> list[PaperCreate]:
    """Run an arXiv API query (search_query or id_list) and parse its entries."""
    resp = client.get(ARXIV_QUERY_URL, params={"start": 0, **params})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [p for entry in root.iter(f"{ATOM}entry") if (p := _parse_arxiv(entry))]


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _parse_s2(item: dict) -> PaperCreate | None:
    title = _clean(item.get("title"))
    if not title:
        return None
    ids = item.get("externalIds") or {}
    pdf = item.get("openAccessPdf") or {}
    return PaperCreate(
        source="s2",
        external_id=item.get("paperId"),
        doi=ids.get("DOI"),
        title=title,
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        year=item.get("year"),
        abstract=_clean(item.get("abstract")),
        url=item.get("url"),
        # S2 often has no open-access link for arXiv papers it knows the id of.
        pdf_url=pdf.get("url") or (f"https://arxiv.org/pdf/{ids['ArXiv']}" if ids.get("ArXiv") else None),
    )


def _parse_arxiv(entry: ET.Element) -> PaperCreate | None:
    entry_id = entry.findtext(f"{ATOM}id") or ""
    title = _clean(entry.findtext(f"{ATOM}title"))
    # arXiv reports query errors as an entry whose id points at /api/errors.
    if "/abs/" not in entry_id or not title:
        return None

    url = pdf_url = None
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel") == "alternate":
            url = link.get("href")
        elif link.get("title") == "pdf":
            pdf_url = link.get("href")

    published = entry.findtext(f"{ATOM}published") or ""
    return PaperCreate(
        source="arxiv",
        external_id=re.sub(r"v\d+$", "", entry_id.split("/abs/", 1)[1]),
        doi=_clean(entry.findtext(f"{ARXIV}doi")),
        title=title,
        authors=[
            name
            for author in entry.findall(f"{ATOM}author")
            if (name := _clean(author.findtext(f"{ATOM}name")))
        ],
        year=int(published[:4]) if published[:4].isdigit() else None,
        abstract=_clean(entry.findtext(f"{ATOM}summary")),
        url=url or entry_id,
        pdf_url=pdf_url,
    )


def get_search_service() -> Iterator[SearchService]:
    with http.make_client() as client:
        yield SearchService(client, settings.semantic_scholar_api_key)
