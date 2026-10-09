import httpx
import pytest

from app.main import app
from app.services.search import SearchError, SearchService, get_search_service

S2_RESPONSE = {
    "total": 2,
    "offset": 0,
    "data": [
        {
            "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "title": "Attention is All you Need",
            "authors": [{"authorId": "1", "name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
            "year": 2017,
            "abstract": "The dominant sequence transduction models\n  are based on ...",
            "url": "https://www.semanticscholar.org/paper/204e3073",
            "externalIds": {"DOI": "10.48550/arXiv.1706.03762", "ArXiv": "1706.03762"},
            "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762", "status": "GREEN"},
        },
        {
            "paperId": "abc",
            "title": "No PDF Paper",
            "authors": [],
            "year": None,
            "abstract": None,
            "url": "https://www.semanticscholar.org/paper/abc",
            "externalIds": None,
            "openAccessPdf": {"url": "", "status": None},
        },
        {"paperId": "untitled", "title": None},
    ],
}

ARXIV_RESPONSE = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2209.15001v3</id>
    <title>Dilated Neighborhood
      Attention Transformer</title>
    <link href="https://arxiv.org/abs/2209.15001v3" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2209.15001v3" rel="related" type="application/pdf" title="pdf"/>
    <summary>Transformers are quickly becoming ...</summary>
    <published>2022-09-29T17:57:08Z</published>
    <arxiv:doi>10.1000/DiNAT</arxiv:doi>
    <author><name>Ali Hassani</name></author>
    <author><name>Humphrey Shi</name></author>
  </entry>
</feed>"""


def make_service(s2=None, arxiv=None, api_key=None, calls=None):
    """A SearchService whose HTTP is answered by the given handlers."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "api.semanticscholar.org":
            return s2(request) if s2 else httpx.Response(500)
        if request.url.host == "export.arxiv.org":
            return arxiv(request) if arxiv else httpx.Response(500)
        raise AssertionError(f"unexpected request to {request.url}")

    return SearchService(httpx.Client(transport=httpx.MockTransport(handler)), api_key)


def s2_ok(request):
    return httpx.Response(200, json=S2_RESPONSE)


def arxiv_ok(request):
    return httpx.Response(200, content=ARXIV_RESPONSE)


@pytest.fixture()
def use_service(client):
    def install(service):
        app.dependency_overrides[get_search_service] = lambda: service

    yield install
    app.dependency_overrides.pop(get_search_service, None)


def test_semantic_scholar_results_are_normalised():
    calls = []
    outcome = make_service(s2=s2_ok, api_key="secret", calls=calls).search("attention", 5)

    assert outcome.provider == "s2"
    assert outcome.fallback_reason is None
    assert [p.title for p in outcome.results] == ["Attention is All you Need", "No PDF Paper"]
    first, second = outcome.results
    assert first.source == "s2"
    assert first.external_id == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    assert first.doi == "10.48550/arXiv.1706.03762"
    assert first.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.year == 2017
    assert first.abstract == "The dominant sequence transduction models are based on ..."
    assert first.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert second.pdf_url is None and second.doi is None and second.abstract is None

    (request,) = calls
    assert request.headers["x-api-key"] == "secret"
    assert request.url.params["query"] == "attention"
    assert request.url.params["limit"] == "5"


def test_s2_arxiv_papers_without_open_access_link_get_arxiv_pdf():
    item = {**S2_RESPONSE["data"][0], "openAccessPdf": None}
    service = make_service(s2=lambda r: httpx.Response(200, json={"data": [item]}))
    (paper,) = service.search("attention", 5).results
    assert paper.pdf_url == "https://arxiv.org/pdf/1706.03762"


@pytest.mark.parametrize(
    "s2",
    [
        lambda r: httpx.Response(429, json={"message": "Too Many Requests"}),
        lambda r: httpx.Response(200, json={"total": 0, "offset": 0}),
        lambda r: httpx.Response(200, content=b"<html>not json</html>"),
    ],
    ids=["rate-limited", "no-results", "bad-json"],
)
def test_falls_back_to_arxiv(s2):
    calls = []
    outcome = make_service(s2=s2, arxiv=arxiv_ok, calls=calls).search("neighborhood attention", 3)

    assert outcome.provider == "arxiv"
    assert outcome.fallback_reason.startswith("Semantic Scholar")
    (paper,) = outcome.results
    assert paper.source == "arxiv"
    assert paper.external_id == "2209.15001"  # version suffix dropped
    assert paper.title == "Dilated Neighborhood Attention Transformer"
    assert paper.authors == ["Ali Hassani", "Humphrey Shi"]
    assert paper.year == 2022
    assert paper.doi == "10.1000/DiNAT"
    assert paper.url == "https://arxiv.org/abs/2209.15001v3"
    assert paper.pdf_url == "https://arxiv.org/pdf/2209.15001v3"
    assert calls[-1].url.params["search_query"] == "all:neighborhood AND all:attention"
    assert calls[-1].url.params["max_results"] == "3"


def test_arxiv_error_entries_are_skipped():
    error_feed = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/api/errors#incorrect_id</id><title>Error</title></entry>
    </feed>"""
    service = make_service(arxiv=lambda r: httpx.Response(200, content=error_feed))
    assert service.search("x", 5).results == []


def test_both_providers_down_raises():
    with pytest.raises(SearchError, match="HTTP 500.*arXiv unavailable"):
        make_service().search("anything", 5)


def test_search_endpoint_returns_results(client, use_service):
    use_service(make_service(s2=s2_ok))
    resp = client.get("/api/search", params={"q": "attention"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "s2"
    assert body["fallback_reason"] is None
    assert len(body["results"]) == 2
    assert all(r["saved_id"] is None for r in body["results"])


def test_search_endpoint_marks_saved_papers(client, use_service, network):
    network.on("arxiv.org", lambda r: httpx.Response(404))  # saving starts a PDF download
    use_service(make_service(s2=s2_ok))
    first = client.get("/api/search", params={"q": "attention"}).json()["results"][0]
    saved = client.post("/api/papers", json=first).json()

    results = client.get("/api/search", params={"q": "attention"}).json()["results"]
    assert [r["saved_id"] for r in results] == [saved["id"], None]


def test_search_endpoint_reports_outage_as_502(client, use_service):
    use_service(make_service())
    resp = client.get("/api/search", params={"q": "attention"})
    assert resp.status_code == 502
    assert "arXiv unavailable" in resp.json()["detail"]


@pytest.mark.parametrize("q", ["", "   "])
def test_search_endpoint_rejects_blank_query(client, use_service, q):
    use_service(make_service(s2=s2_ok))
    assert client.get("/api/search", params={"q": q}).status_code == 422
