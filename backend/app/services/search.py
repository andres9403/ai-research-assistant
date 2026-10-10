"""Paper search: Semantic Scholar first, arXiv as fallback.

Results are normalised to `SearchResult` so the client can send one straight
back to `POST /api/papers`. Nothing here touches the database.

Filters go upstream only where the provider supports them. A filter one
provider can't apply is either applied here to a larger page of its results
(author and ordering on Semantic Scholar) or reported as unapplied.
"""

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.services import http
from app.schemas import SearchFilters, SearchResult

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "paperId,title,authors,year,abstract,url,externalIds,openAccessPdf,venue,citationCount"
S2_MAX_LIMIT = 100  # the most /paper/search returns in one page
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
    results: list[SearchResult]
    fallback_reason: str | None = None
    unapplied_filters: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ProviderRequest:
    """What one provider is asked for, and what it could not be asked."""

    params: dict
    flags: list[str] = field(default_factory=list)  # valueless query parameters
    unapplied_filters: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return " ".join(text.split()) or None


class SearchService:
    def __init__(self, client: httpx.Client, s2_api_key: str | None = None):
        self.client = client
        self.s2_api_key = s2_api_key

    def search(self, query: str, limit: int = 20, filters: SearchFilters | None = None) -> SearchOutcome:
        filters = filters or SearchFilters()
        try:
            request = s2_request(query, limit, filters)
            results = self.search_semantic_scholar(query, limit, filters)
            if results:
                return SearchOutcome("s2", results, None, request.unapplied_filters, request.notes)
            reason = "Semantic Scholar returned no results"
            if filters != SearchFilters():
                reason += " matching the filters"
        except (httpx.HTTPError, ValueError) as exc:
            reason = f"Semantic Scholar unavailable ({_describe(exc)})"

        try:
            request = arxiv_request(query, limit, filters)
            results = self.search_arxiv(query, limit, filters)
            return SearchOutcome("arxiv", results, reason, request.unapplied_filters, request.notes)
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise SearchError(f"{reason}; arXiv unavailable ({_describe(exc)})") from exc

    def search_semantic_scholar(
        self, query: str, limit: int, filters: SearchFilters | None = None
    ) -> list[SearchResult]:
        filters = filters or SearchFilters()
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}
        request = s2_request(query, limit, filters)
        url = httpx.URL(S2_SEARCH_URL, params=request.params)
        if request.flags:
            url = url.copy_with(query=url.query + "".join(f"&{flag}" for flag in request.flags).encode())
        resp = self.client.get(url, headers=headers)
        resp.raise_for_status()
        results = [p for item in resp.json().get("data") or [] if (p := _parse_s2(item))]
        if filters.author:
            results = [p for p in results if matches_author(p.authors, filters.author)]
        if filters.sort == "newest":
            results.sort(key=lambda p: -(p.year or 0))  # stable, so relevance breaks ties
        elif filters.sort == "citations":
            results.sort(key=lambda p: -(p.citation_count or 0))
        return results[:limit]

    def search_arxiv(self, query: str, limit: int, filters: SearchFilters | None = None) -> list[SearchResult]:
        request = arxiv_request(query, limit, filters or SearchFilters())
        if not request.params:
            return []
        return query_arxiv(self.client, request.params)


def s2_request(query: str, limit: int, filters: SearchFilters) -> ProviderRequest:
    """Semantic Scholar's /paper/search parameters for this search.

    It filters by year, venue, type and open access itself, but has no author
    filter and only ranks by relevance, so those are applied locally to a full
    page of its best matches.
    """
    params = {"query": query, "limit": limit, "fields": S2_FIELDS}
    if filters.year_from or filters.year_to:
        params["year"] = f"{filters.year_from or ''}-{filters.year_to or ''}"
    if filters.venue:
        params["venue"] = filters.venue
    if filters.publication_type:
        params["publicationTypes"] = filters.publication_type
    # Semantic Scholar's openAccessPdf filter takes no value.
    flags = ["openAccessPdf"] if filters.open_access else []

    local = []
    if filters.author:
        local.append("the author filter")
    if filters.sort != "relevance":
        local.append("the ordering")
    notes = []
    if local:
        params["limit"] = max(limit, S2_MAX_LIMIT)
        notes.append(
            f"Semantic Scholar can't do this itself, so {' and '.join(local)} "
            f"{'were' if len(local) > 1 else 'was'} applied to its {params['limit']} most relevant matches."
        )
    return ProviderRequest(params, flags=flags, notes=notes)


def arxiv_request(query: str, limit: int, filters: SearchFilters) -> ProviderRequest:
    """arXiv API parameters for this search; empty when the query has no searchable words.

    arXiv filters by author and submission date and sorts by date, but has no
    venues, paper types or citation counts. Every arXiv paper is open access.
    """
    # AND the words together; arXiv's default OR ranks loosely related papers first.
    words = arxiv_terms(query)
    if not words:
        return ProviderRequest({})
    clauses = [f"all:{w}" for w in words]
    if filters.author:
        name = " ".join(re.findall(r"[\w-]+", filters.author))
        if name:
            clauses.append(f'au:"{name}"')
    if filters.year_from or filters.year_to:
        clauses.append(f"submittedDate:[{filters.year_from or 1900}01010000 TO {filters.year_to or 2100}12312359]")
    params = {"search_query": " AND ".join(clauses), "max_results": limit}

    unapplied, notes = [], []
    if filters.sort == "newest":
        params |= {"sortBy": "submittedDate", "sortOrder": "descending"}
    elif filters.sort == "citations":
        unapplied.append("sort")
        notes.append("arXiv has no citation counts, so results are in relevance order.")
    missing = {"venue": "venue", "publication_type": "paper type"}
    skipped = [key for key in missing if getattr(filters, key)]
    if skipped:
        unapplied += skipped
        labels = " or ".join(missing[key] for key in skipped)
        notes.append(
            f"arXiv can't filter by {labels}, so {'those filters were' if len(skipped) > 1 else 'that filter was'} "
            "not applied."
        )
    return ProviderRequest(params, unapplied_filters=unapplied, notes=notes)


def matches_author(authors: list[str], wanted: str) -> bool:
    """True if one author's name has every word of `wanted`, each as a word prefix.

    Case and accents are ignored, so "a vaswani" and "Vaswani, A." both match
    "Ashish Vaswani".
    """
    wanted_words = _name_words(wanted)
    if not wanted_words:
        return True
    return any(
        all(any(word.startswith(w) for word in words) for w in wanted_words)
        for words in map(_name_words, authors)
    )


def _name_words(name: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return re.findall(r"\w+", plain.casefold())


def arxiv_terms(text: str) -> list[str]:
    words = re.findall(r"[\w-]+", text)
    return [w for w in words if w.lower() not in ARXIV_STOPWORDS] or words


def query_arxiv(client: httpx.Client, params: dict) -> list[SearchResult]:
    """Run an arXiv API query (search_query or id_list) and parse its entries."""
    resp = client.get(ARXIV_QUERY_URL, params={"start": 0, **params})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [p for entry in root.iter(f"{ATOM}entry") if (p := _parse_arxiv(entry))]


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _parse_s2(item: dict) -> SearchResult | None:
    title = _clean(item.get("title"))
    if not title:
        return None
    ids = item.get("externalIds") or {}
    pdf = item.get("openAccessPdf") or {}
    return SearchResult(
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
        venue=_clean(item.get("venue")),
        citation_count=item.get("citationCount"),
    )


def _parse_arxiv(entry: ET.Element) -> SearchResult | None:
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
    return SearchResult(
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
