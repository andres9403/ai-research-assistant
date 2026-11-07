"""Tests for services/search.py and GET /api/search, with Semantic Scholar and arXiv mocked."""

import httpx
import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas import SearchFilters
from app.services import search
from app.services.search import (
    SearchError,
    SearchService,
    arxiv_request,
    arxiv_terms,
    get_search_service,
    matches_author,
    s2_request,
)

from tests_hw2.helpers import add_paper, arxiv_feed, json_response, mock_client

S2_ITEMS = [
    {
        "paperId": "s2-1",
        "title": "  Attention   Is All You Need ",
        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}, {"name": None}],
        "year": 2017,
        "abstract": "The dominant  sequence models...",
        "url": "https://www.semanticscholar.org/paper/s2-1",
        "externalIds": {"DOI": "10.5555/attn", "ArXiv": "1706.03762"},
        "openAccessPdf": None,
        "venue": "NeurIPS",
        "citationCount": 100000,
    },
    {
        "paperId": "s2-2",
        "title": "BERT",
        "authors": [{"name": "Jacob Devlin"}],
        "year": 2019,
        "externalIds": {},
        "openAccessPdf": {"url": "https://example.org/bert.pdf"},
        "citationCount": 90000,
    },
    {"paperId": "s2-3", "title": "", "authors": []},  # untitled entries are dropped
    {
        "paperId": "s2-4",
        "title": "Newer Work",
        "authors": [{"name": "José Álvarez"}],
        "year": 2023,
        "citationCount": 5,
    },
]

ARXIV_ENTRIES = [
    {"id": "2101.00001v2", "title": "An arXiv Paper", "authors": ["Ann Lee"], "published": "2021-01-01T00:00:00Z"},
    {"id": "2202.00002v1", "title": "Another arXiv Paper", "authors": ["Bo Kim"], "doi": "10.1/arx"},
]


class Upstream:
    """Mock Semantic Scholar and arXiv; records every request."""

    def __init__(self, s2=None, arxiv=None):
        self.s2 = s2 if s2 is not None else json_response({"data": S2_ITEMS})
        self.arxiv = arxiv if arxiv is not None else httpx.Response(200, text=arxiv_feed(ARXIV_ENTRIES))
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.semanticscholar.org":
            response = self.s2
        elif request.url.host == "export.arxiv.org":
            response = self.arxiv
        else:  # pragma: no cover - a test bug
            raise AssertionError(f"unexpected request {request.url}")
        if isinstance(response, Exception):
            raise response
        return response

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


def service(upstream: Upstream, key: str | None = None) -> SearchService:
    return SearchService(mock_client(upstream), key)


# ----------------------------------------------------------------------- service


def test_semantic_scholar_results_are_normalised():
    upstream = Upstream()
    outcome = service(upstream).search("attention", 10)
    assert outcome.provider == "s2"
    assert outcome.fallback_reason is None
    assert [r.title for r in outcome.results] == ["Attention Is All You Need", "BERT", "Newer Work"]
    first = outcome.results[0]
    assert first.source == "s2"
    assert first.external_id == "s2-1"
    assert first.doi == "10.5555/attn"
    assert first.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.abstract == "The dominant sequence models..."
    assert first.pdf_url == "https://arxiv.org/pdf/1706.03762"  # arXiv id fills a missing OA link
    assert first.venue == "NeurIPS"
    assert first.citation_count == 100000
    assert outcome.results[1].pdf_url == "https://example.org/bert.pdf"
    assert upstream.hosts() == ["api.semanticscholar.org"]


def test_semantic_scholar_request_carries_query_limit_and_api_key():
    upstream = Upstream()
    service(upstream, key="my-key").search("graph networks", 7)
    request = upstream.requests[0]
    assert request.url.params["query"] == "graph networks"
    assert request.url.params["limit"] == "7"
    assert request.headers["x-api-key"] == "my-key"


def test_no_api_key_header_without_a_key():
    upstream = Upstream()
    service(upstream).search("x", 5)
    assert "x-api-key" not in upstream.requests[0].headers


def test_falls_back_to_arxiv_when_semantic_scholar_fails():
    upstream = Upstream(s2=httpx.Response(500))
    outcome = service(upstream).search("transformers", 5)
    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason == "Semantic Scholar unavailable (HTTP 500)"
    assert outcome.suggest_s2_key is False
    assert [r.external_id for r in outcome.results] == ["2101.00001", "2202.00002"]  # version stripped
    first = outcome.results[0]
    assert first.source == "arxiv"
    assert first.year == 2021
    assert first.pdf_url == "http://arxiv.org/pdf/2101.00001v2"
    assert outcome.results[1].doi == "10.1/arx"
    assert upstream.hosts() == ["api.semanticscholar.org", "export.arxiv.org"]


def test_falls_back_on_connection_error():
    upstream = Upstream(s2=httpx.ConnectError("down"))
    outcome = service(upstream).search("transformers", 5)
    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason == "Semantic Scholar unavailable (ConnectError)"


def test_rate_limited_keyless_search_suggests_a_key():
    outcome = service(Upstream(s2=httpx.Response(429))).search("x", 5)
    assert outcome.provider == "arxiv"
    assert outcome.suggest_s2_key is True
    keyed = service(Upstream(s2=httpx.Response(429)), key="k").search("x", 5)
    assert keyed.suggest_s2_key is False


def test_empty_semantic_scholar_results_fall_back_to_arxiv():
    outcome = service(Upstream(s2=json_response({"data": []}))).search("x", 5)
    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason == "Semantic Scholar returned no results"
    filtered = service(Upstream(s2=json_response({"data": None}))).search("x", 5, SearchFilters(venue="ICML"))
    assert filtered.fallback_reason == "Semantic Scholar returned no results matching the filters"


def test_invalid_json_from_semantic_scholar_falls_back():
    outcome = service(Upstream(s2=httpx.Response(200, text="<html>oops</html>"))).search("x", 5)
    assert outcome.provider == "arxiv"


def test_both_providers_failing_raises_search_error():
    upstream = Upstream(s2=httpx.Response(503), arxiv=httpx.Response(500))
    with pytest.raises(SearchError) as info:
        service(upstream).search("x", 5)
    assert "Semantic Scholar unavailable (HTTP 503)" in str(info.value)
    assert "arXiv unavailable (HTTP 500)" in str(info.value)


def test_malformed_arxiv_xml_raises_search_error():
    upstream = Upstream(s2=httpx.Response(500), arxiv=httpx.Response(200, text="<feed><unclosed>"))
    with pytest.raises(SearchError, match="ParseError"):
        service(upstream).search("x", 5)


def test_arxiv_error_entries_are_ignored():
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id>'
        "<title>Error</title></entry></feed>"
    )
    outcome = service(Upstream(s2=httpx.Response(500), arxiv=httpx.Response(200, text=feed))).search("x", 5)
    assert outcome.results == []


def test_author_filter_and_sorting_are_applied_locally_on_semantic_scholar():
    upstream = Upstream()
    outcome = service(upstream).search("x", 10, SearchFilters(author="a vaswani"))
    assert [r.title for r in outcome.results] == ["Attention Is All You Need"]
    assert upstream.requests[0].url.params["limit"] == "100"  # a full page to filter
    assert outcome.notes and "author filter" in outcome.notes[0]

    newest = service(Upstream()).search("x", 10, SearchFilters(sort="newest"))
    assert [r.year for r in newest.results] == [2023, 2019, 2017]
    cited = service(Upstream()).search("x", 2, SearchFilters(sort="citations"))
    assert [r.citation_count for r in cited.results] == [100000, 90000]  # trimmed to the limit


def test_semantic_scholar_filters_go_upstream():
    upstream = Upstream()
    filters = SearchFilters(year_from=2015, venue="NeurIPS", publication_type="Conference", open_access=True)
    service(upstream).search("x", 5, filters)
    url = upstream.requests[0].url
    assert url.params["year"] == "2015-"
    assert url.params["venue"] == "NeurIPS"
    assert url.params["publicationTypes"] == "Conference"
    assert b"openAccessPdf" in url.query


# ----------------------------------------------------------------------- request builders


def test_s2_request_year_ranges():
    assert s2_request("q", 5, SearchFilters(year_to=2020)).params["year"] == "-2020"
    assert s2_request("q", 5, SearchFilters(year_from=2010, year_to=2020)).params["year"] == "2010-2020"
    assert "year" not in s2_request("q", 5, SearchFilters()).params


def test_arxiv_request_builds_and_query_with_filters():
    request = arxiv_request("the attention model", 10, SearchFilters(author="Vaswani, A.", year_from=2017))
    query = request.params["search_query"]
    assert query.startswith("all:attention AND all:model")
    assert 'au:"Vaswani A"' in query
    assert "submittedDate:[201701010000 TO 210012312359]" in query
    assert request.params["max_results"] == 10


def test_arxiv_request_reports_unsupported_filters():
    request = arxiv_request("x", 5, SearchFilters(venue="ICML", publication_type="Review", sort="citations"))
    assert set(request.unapplied_filters) == {"venue", "publication_type", "sort"}
    assert len(request.notes) == 2
    newest = arxiv_request("x", 5, SearchFilters(sort="newest"))
    assert newest.params["sortBy"] == "submittedDate"
    assert newest.unapplied_filters == []


def test_arxiv_request_is_empty_for_a_query_without_words():
    assert arxiv_request("!!! ???", 5, SearchFilters()).params == {}


def test_wordless_query_returns_no_arxiv_results_without_querying_arxiv():
    upstream = Upstream(s2=json_response({"data": []}))
    outcome = service(upstream).search("!!! ???", 5)
    assert outcome.provider == "arxiv"
    assert outcome.results == []
    assert upstream.hosts() == ["api.semanticscholar.org"]  # arXiv would match nothing, so it isn't asked


@pytest.mark.parametrize(
    "text, expected",
    [
        ("the attention of models", ["attention", "models"]),
        ("the of and", ["the", "of", "and"]),  # all stopwords: keep them rather than search nothing
        ("self-attention", ["self-attention"]),
        ("", []),
    ],
)
def test_arxiv_terms(text, expected):
    assert arxiv_terms(text) == expected


@pytest.mark.parametrize(
    "wanted, expected",
    [
        ("vaswani", True),
        ("A Vaswani", True),
        ("Vaswani, A.", True),
        ("ash vas", True),
        ("shazeer vaswani", False),  # words must all be in one author's name
        ("alvarez", True),  # accents ignored
        ("smith", False),
        ("  ", True),
    ],
)
def test_matches_author(wanted, expected):
    assert matches_author(["Ashish Vaswani", "Noam Shazeer", "José Álvarez"], wanted) is expected


# ----------------------------------------------------------------------- schema


def test_search_filters_validation():
    assert SearchFilters(author="  Ann   Lee ").author == "Ann Lee"
    assert SearchFilters(author="   ").author is None
    with pytest.raises(ValidationError):
        SearchFilters(venue="ICML, NeurIPS")
    with pytest.raises(ValidationError):
        SearchFilters(year_from=2020, year_to=2010)
    with pytest.raises(ValidationError):
        SearchFilters(year_from=1800)
    assert SearchFilters(year_from=2020, year_to=2020).year_to == 2020


# ----------------------------------------------------------------------- API


@pytest.fixture
def upstream(client):
    mock = Upstream()
    app.dependency_overrides[get_search_service] = lambda: SearchService(mock_client(mock), None)
    return mock


def test_search_endpoint_returns_results_and_marks_saved_papers(client, session, upstream):
    saved = add_paper(session, title="BERT", source="s2", external_id="s2-2")
    response = client.get("/api/search", params={"q": "  attention  ", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "s2"
    assert body["fallback_reason"] is None
    assert body["suggest_s2_key"] is False
    by_title = {r["title"]: r for r in body["results"]}
    assert by_title["BERT"]["saved_id"] == saved.id
    assert by_title["Attention Is All You Need"]["saved_id"] is None
    assert upstream.requests[0].url.params["query"] == "attention"  # trimmed


def test_search_result_can_be_saved_and_is_then_marked(client, upstream):
    result = client.get("/api/search", params={"q": "attention"}).json()["results"][0]
    payload = {k: v for k, v in result.items() if k not in ("saved_id", "venue", "citation_count")}
    payload["pdf_url"] = None  # keep the test free of background downloads
    saved = client.post("/api/papers", json=payload)
    assert saved.status_code == 201
    again = client.get("/api/search", params={"q": "attention"}).json()["results"][0]
    assert again["saved_id"] == saved.json()["id"]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 301},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 51},
        {"q": "x", "year_from": 2020, "year_to": 2010},
        {"q": "x", "year_from": 1850},
        {"q": "x", "venue": "A, B"},
        {"q": "x", "sort": "random"},
        {"q": "x", "publication_type": "Blog"},
        {"q": "x", "author": "a" * 101},
    ],
)
def test_search_endpoint_validates_parameters(client, upstream, params):
    assert client.get("/api/search", params=params).status_code == 422
    assert upstream.requests == []


def test_search_endpoint_returns_502_when_every_provider_fails(client):
    mock = Upstream(s2=httpx.Response(500), arxiv=httpx.Response(502))
    app.dependency_overrides[get_search_service] = lambda: SearchService(mock_client(mock), None)
    response = client.get("/api/search", params={"q": "x"})
    assert response.status_code == 502
    assert "arXiv unavailable" in response.json()["detail"]


def test_search_endpoint_reports_fallback_and_unapplied_filters(client):
    mock = Upstream(s2=httpx.Response(429))
    app.dependency_overrides[get_search_service] = lambda: SearchService(mock_client(mock), None)
    body = client.get("/api/search", params={"q": "x", "venue": "ICML"}).json()
    assert body["provider"] == "arxiv"
    assert body["suggest_s2_key"] is True
    assert body["unapplied_filters"] == ["venue"]
    assert body["fallback_reason"].startswith("Semantic Scholar unavailable (HTTP 429)")


def test_default_search_service_uses_the_configured_key(monkeypatch):
    monkeypatch.setattr(search.settings, "semantic_scholar_api_key", "configured")
    gen = get_search_service()
    svc = next(gen)
    assert svc.s2_api_key == "configured"
    gen.close()
