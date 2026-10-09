import httpx
import pytest

from app.services.metadata import (
    ExtractedMetadata,
    _split_names,
    extract_metadata,
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
    assert meta.source == "semantic_scholar"
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
