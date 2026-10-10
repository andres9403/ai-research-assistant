import httpx
import pytest

from app.schemas import SearchFilters
from app.services.search import arxiv_request, matches_author, s2_request
from tests.test_search import S2_RESPONSE, arxiv_ok, make_service, s2_ok, use_service  # noqa: F401


def s2_papers(*papers):
    """An S2 handler answering with papers given as (title, authors, year, citations)."""
    data = [
        {"paperId": title, "title": title, "authors": [{"name": a} for a in authors], "year": year,
         "citationCount": citations, "venue": "NeurIPS"}
        for title, authors, year, citations in papers
    ]
    return lambda request: httpx.Response(200, json={"data": data})


# Request construction


def test_no_filters_sends_the_plain_keyword_requests():
    s2 = s2_request("attention", 20, SearchFilters())
    assert set(s2.params) == {"query", "limit", "fields"}
    assert s2.params["limit"] == 20
    arxiv = arxiv_request("attention", 20, SearchFilters())
    assert arxiv.params == {"search_query": "all:attention", "max_results": 20}
    assert s2.notes == s2.unapplied_filters == arxiv.notes == arxiv.unapplied_filters == []


def test_s2_gets_the_filters_it_supports():
    filters = SearchFilters(
        year_from=2017, year_to=2019, venue="NeurIPS", publication_type="Conference", open_access=True
    )
    request = s2_request("attention", 20, filters)
    assert request.params["year"] == "2017-2019"
    assert request.params["venue"] == "NeurIPS"
    assert request.params["publicationTypes"] == "Conference"
    assert request.flags == ["openAccessPdf"] and "openAccessPdf" not in request.params
    assert request.params["limit"] == 20  # nothing applied locally
    assert request.notes == [] and request.unapplied_filters == []


@pytest.mark.parametrize(("year_from", "year_to", "expected"), [(2017, None, "2017-"), (None, 2019, "-2019")])
def test_s2_open_year_ranges(year_from, year_to, expected):
    assert s2_request("x", 5, SearchFilters(year_from=year_from, year_to=year_to)).params["year"] == expected


def test_s2_author_and_ordering_are_applied_locally_to_a_full_page():
    request = s2_request("attention", 20, SearchFilters(author="Vaswani", sort="citations"))
    assert "author" not in request.params and "sort" not in request.params
    assert request.params["limit"] == 100
    assert request.notes == [
        "Semantic Scholar has no author filter or sort option, so they were applied to its "
        "100 most relevant matches."
    ]


def test_arxiv_gets_author_dates_and_date_order():
    filters = SearchFilters(author="Ashish Vaswani", year_from=2017, year_to=2018, sort="newest")
    request = arxiv_request("attention", 10, filters)
    assert request.params == {
        "search_query": 'all:attention AND au:"Ashish Vaswani" '
        "AND submittedDate:[201701010000 TO 201812312359]",
        "max_results": 10,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    assert request.unapplied_filters == []


def test_arxiv_reports_filters_it_cannot_apply():
    filters = SearchFilters(venue="NeurIPS", publication_type="Review", open_access=True, sort="citations")
    request = arxiv_request("attention", 10, filters)
    assert request.params == {"search_query": "all:attention", "max_results": 10}
    assert request.unapplied_filters == ["sort", "venue", "publication_type"]
    assert request.notes == [
        "arXiv has no citation counts, so results are in relevance order.",
        "arXiv can't filter by venue or paper type, so those filters were not applied.",
    ]


def test_arxiv_author_strips_query_syntax():
    request = arxiv_request("attention", 10, SearchFilters(author='Vaswani" OR au:"x'))
    assert request.params["search_query"] == 'all:attention AND au:"Vaswani OR au x"'


@pytest.mark.parametrize(
    ("wanted", "expected"),
    [("Vaswani", True), ("ashish vaswani", True), ("Vaswani, A.", True), ("Shazeer Vaswani", False),
     ("Gomez", False), ("", True)],
)
def test_author_matching(wanted, expected):
    assert matches_author(["Ashish Vaswani", "Noam Shazeer"], wanted) is expected


def test_author_matching_ignores_accents():
    assert matches_author(["José Hernández-Orallo"], "jose hernandez")


# Search behaviour


def test_s2_results_are_filtered_by_author_and_sorted_locally():
    service = make_service(s2=s2_papers(
        ("Old", ["Ashish Vaswani"], 2017, 900),
        ("Other author", ["Someone Else"], 2023, 5000),
        ("New", ["A. Vaswani"], 2021, 10),
        ("Undated", ["Ashish Vaswani"], None, None),
    ))
    newest = service.search("attention", 2, SearchFilters(author="vaswani", sort="newest"))
    assert newest.provider == "s2"
    assert [p.title for p in newest.results] == ["New", "Old"]
    assert newest.notes and newest.unapplied_filters == []

    cited = service.search("attention", 5, SearchFilters(sort="citations"))
    assert [p.title for p in cited.results] == ["Other author", "Old", "New", "Undated"]
    assert cited.results[0].citation_count == 5000 and cited.results[0].venue == "NeurIPS"


def test_fallback_to_arxiv_forwards_filters_and_reports_the_rest():
    calls = []
    filters = SearchFilters(author="Hassani", year_from=2022, venue="CVPR")
    outcome = make_service(arxiv=arxiv_ok, calls=calls).search("neighborhood attention", 3, filters)

    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason.startswith("Semantic Scholar unavailable")
    assert outcome.unapplied_filters == ["venue"]
    s2_call, arxiv_call = calls
    assert s2_call.url.params["year"] == "2022-" and s2_call.url.params["venue"] == "CVPR"
    assert arxiv_call.url.params["search_query"] == (
        'all:neighborhood AND all:attention AND au:"Hassani" '
        "AND submittedDate:[202201010000 TO 210012312359]"
    )


def test_s2_with_no_author_match_falls_back_to_arxiv():
    service = make_service(s2=s2_ok, arxiv=arxiv_ok)
    outcome = service.search("attention", 5, SearchFilters(author="Hassani"))
    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason == "Semantic Scholar returned no results matching the filters"


# API


def test_search_endpoint_forwards_filters(client, use_service):
    calls = []
    use_service(make_service(s2=s2_ok, calls=calls))
    resp = client.get("/api/search", params={
        "q": "attention", "author": "  Ashish   Vaswani ", "year_from": 2017, "year_to": 2017,
        "open_access": "true", "sort": "citations",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "s2"
    assert [r["title"] for r in body["results"]] == ["Attention is All you Need"]
    assert body["notes"] and body["unapplied_filters"] == []
    params = calls[0].url.params
    assert params["year"] == "2017-2017" and params["limit"] == "100"
    assert calls[0].url.query.decode().endswith("&openAccessPdf")  # a bare flag, as the API requires


def test_search_endpoint_without_filters_is_keyword_only(client, use_service):
    calls = []
    use_service(make_service(s2=s2_ok, calls=calls))
    body = client.get("/api/search", params={"q": "attention", "author": "   ", "venue": ""}).json()
    assert body["notes"] == [] and body["unapplied_filters"] == []
    assert set(calls[0].url.params) == {"query", "limit", "fields"}


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"year_from": 2020, "year_to": 2010}, "year_from must not be later than year_to"),
        ({"year_from": 1500}, "greater than or equal to 1900"),
        ({"year_to": "soon"}, "valid integer"),
        ({"venue": "NeurIPS, ICML"}, "single name"),
        ({"publication_type": "Blog"}, "JournalArticle"),
        ({"sort": "random"}, "relevance"),
        ({"author": "x" * 101}, "at most 100 characters"),
    ],
)
def test_search_endpoint_validates_filters(client, use_service, params, message):
    calls = []
    use_service(make_service(s2=s2_ok, calls=calls))
    resp = client.get("/api/search", params={"q": "attention", **params})
    assert resp.status_code == 422
    assert message in str(resp.json()["detail"])
    assert calls == []  # nothing reaches the providers
