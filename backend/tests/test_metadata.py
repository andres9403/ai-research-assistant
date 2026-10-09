import httpx
import pytest

from app.services.metadata import (
    ExtractedMetadata,
    _split_names,
    extract_metadata,
    lookup_arxiv,
    lookup_semantic_scholar,
)
from app.services.pdf import extract
from tests.pdf_factory import ABSTRACT, AUTHORS, TITLE, make_ieee_pdf, make_paper_pdf, make_scanned_pdf


def test_metadata_from_first_page():
    meta = extract_metadata(extract(make_paper_pdf()))
    assert meta.title == TITLE
    assert meta.authors == AUTHORS
    assert meta.year == 2024  # from the arXiv stamp
    assert meta.arxiv_id == "2401.01234"
    assert meta.url == "https://arxiv.org/abs/2401.01234"
    assert meta.abstract == ABSTRACT
    assert meta.source == "pdf"


def test_metadata_without_arxiv_stamp_uses_years_on_the_page():
    meta = extract_metadata(extract(make_paper_pdf(arxiv_stamp=None)))
    assert meta.arxiv_id is None
    assert meta.year is None  # the first page mentions no year
    assert meta.title == TITLE


def test_metadata_for_ieee_style_paper():
    meta = extract_metadata(extract(make_ieee_pdf()))
    assert meta.title == "Graph Neural Networks for Molecule Property Prediction"
    assert meta.authors == ["Maria Rossi", "Kenji Tanaka"]
    assert meta.abstract.startswith("We predict molecular properties")


def test_scanned_pdf_yields_empty_metadata():
    meta = extract_metadata(extract(make_scanned_pdf()))
    assert meta.title is None and meta.authors == [] and meta.abstract is None


@pytest.mark.parametrize(
    "line, names",
    [
        ("Ali Hassani1,2, Humphrey Shi1", ["Ali Hassani", "Humphrey Shi"]),
        ("Ashish Vaswani∗  Noam Shazeer†", ["Ashish Vaswani", "Noam Shazeer"]),
        ("Ludwig van Beethoven and Wen-tau Yih", ["Ludwig van Beethoven", "Wen-tau Yih"]),
        ("We propose a new method", []),
        ("Section 3", []),
    ],
)
def test_split_names(line, names):
    assert _split_names(line) == names


S2_PAPER = {
    "paperId": "s2abc",
    "title": "Sparse Expert Routing for Efficient Language Models",
    "authors": [{"name": "Ana M. López"}, {"name": "Wei Zhang"}],
    "year": 2023,
    "abstract": "The published abstract.",
    "url": "https://www.semanticscholar.org/paper/s2abc",
    "externalIds": {"DOI": "10.1000/sparse", "ArXiv": "2401.01234"},
}


def lookup(handler, meta):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return lookup_semantic_scholar(client, meta)


def test_lookup_by_arxiv_id_replaces_guesses():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=S2_PAPER)

    meta = lookup(handler, ExtractedMetadata(title="Sparse Expert Routing", arxiv_id="2401.01234"))
    assert seen == ["/graph/v1/paper/arXiv:2401.01234"]
    assert meta.source == "semantic_scholar"
    assert meta.external_source == "s2"
    assert meta.external_id == "s2abc"
    assert meta.title == S2_PAPER["title"]
    assert meta.authors == ["Ana M. López", "Wei Zhang"]
    assert (meta.year, meta.doi, meta.abstract) == (2023, "10.1000/sparse", "The published abstract.")


def test_lookup_falls_back_to_title_match():
    def handler(request):
        if request.url.path.endswith("/search/match"):
            assert request.url.params["query"] == TITLE
            return httpx.Response(200, json={"data": [{**S2_PAPER, "abstract": None}]})
        return httpx.Response(404, json={"error": "not found"})

    original = ExtractedMetadata(title=TITLE, abstract="From the PDF.", arxiv_id="9999.99999")
    meta = lookup(handler, original)
    assert meta.source == "semantic_scholar_title"  # a title search: flagged for the user
    assert meta.abstract == "From the PDF."  # S2 had none, so the extracted one stays


def test_lookup_rejects_a_dissimilar_title_match():
    match = {**S2_PAPER, "title": "A Completely Different Paper About Proteins"}
    meta = ExtractedMetadata(title=TITLE)
    assert lookup(lambda r: httpx.Response(200, json={"data": [match]}), meta) is meta


@pytest.mark.parametrize(
    "response",
    [httpx.Response(429, json={"message": "Too Many Requests"}), httpx.Response(200, content=b"oops")],
    ids=["rate-limited", "bad-json"],
)
def test_lookup_failures_keep_the_heuristic_metadata(response):
    meta = ExtractedMetadata(title=TITLE, doi="10.1000/x")
    assert lookup(lambda r: response, meta) is meta


def test_lookup_survives_network_errors():
    def handler(request):
        raise httpx.ConnectError("offline")

    meta = ExtractedMetadata(title=TITLE)
    assert lookup(handler, meta) is meta


def arxiv_entry(arxiv_id, title):
    return f"""<entry><id>http://arxiv.org/abs/{arxiv_id}v2</id><title>{title}</title>
      <link href="https://arxiv.org/abs/{arxiv_id}v2" rel="alternate" type="text/html"/>
      <summary>arXiv abstract.</summary><published>2023-05-01T00:00:00Z</published>
      <author><name>Ana María López</name></author></entry>"""


def feed(*entries):
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{"".join(entries)}</feed>'.encode()


def arxiv_lookup(handler, meta):
    return lookup_arxiv(httpx.Client(transport=httpx.MockTransport(handler)), meta)


def test_arxiv_lookup_by_id_is_exact():
    def handler(request):
        assert request.url.params["id_list"] == "2401.01234"
        return httpx.Response(200, content=feed(arxiv_entry("2401.01234", "Published Title")))

    meta = arxiv_lookup(handler, ExtractedMetadata(title=TITLE, arxiv_id="2401.01234"))
    assert meta.source == "arxiv"
    assert meta.external_source == "arxiv"
    assert (meta.title, meta.year, meta.abstract) == ("Published Title", 2023, "arXiv abstract.")
    assert meta.url == "https://arxiv.org/abs/2401.01234v2"


def test_arxiv_title_search_picks_the_most_similar_title():
    queries = []

    def handler(request):
        queries.append(request.url.params["search_query"])
        return httpx.Response(200, content=feed(
            arxiv_entry("2501.00001", "Not All Sparse Expert Routing for Efficient Language Models"),
            arxiv_entry("1706.03762", "Sparse Expert Routing for Efficient Language Models."),
        ))

    meta = arxiv_lookup(handler, ExtractedMetadata(title=TITLE, authors=["From PDF"]))
    assert queries == ['ti:"sparse expert routing for efficient language models"']
    assert meta.source == "arxiv_title"
    assert meta.external_id == "1706.03762"
    assert meta.authors == ["Ana María López"]


def test_arxiv_title_search_falls_back_to_words_without_stopwords():
    queries = []

    def handler(request):
        queries.append(request.url.params["search_query"])
        if len(queries) == 1:
            return httpx.Response(200, content=feed())
        return httpx.Response(200, content=feed(arxiv_entry("1706.03762", "Attention Is All You Need")))

    meta = arxiv_lookup(handler, ExtractedMetadata(title="Attention is all you need"))
    assert queries == ['ti:"attention is all you need"', "ti:attention AND ti:need"]
    assert meta.source == "arxiv_title"


def test_arxiv_title_search_rejects_near_misses():
    content = feed(arxiv_entry("2104.04692", "Not All Attention Is All You Need"))
    meta = ExtractedMetadata(title="Attention Is All You Need")
    assert arxiv_lookup(lambda r: httpx.Response(200, content=content), meta) is meta


@pytest.mark.parametrize(
    "response", [httpx.Response(503), httpx.Response(200, content=b"<html>")], ids=["down", "bad-xml"]
)
def test_arxiv_failures_keep_the_metadata(response):
    meta = ExtractedMetadata(title=TITLE, arxiv_id="2401.01234")
    assert arxiv_lookup(lambda r: response, meta) is meta
