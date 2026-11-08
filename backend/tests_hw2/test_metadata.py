"""Tests for services/metadata.py: heuristics over the PDF, the LLM pass and the S2/arXiv lookups."""

from datetime import date

import httpx
import pytest

from app.services.llm import LLMError
from app.services.metadata import (
    ExtractedMetadata,
    extract_metadata,
    llm_metadata,
    lookup,
    lookup_arxiv,
    lookup_semantic_scholar,
)
from app.services.pdf import extract

from tests_hw2.helpers import FakeLLM, PAPER_TITLE, arxiv_feed, build_pdf, json_response, mock_client, paper_pdf

# ----------------------------------------------------------------------- heuristics


def test_heuristics_find_title_authors_and_abstract():
    meta = extract_metadata(extract(paper_pdf()))
    assert meta.title == PAPER_TITLE
    assert meta.authors == ["Alice Smith", "Bob Jones"]
    assert meta.abstract.startswith("We study widgets in detail")
    assert meta.source == "pdf"
    assert meta.doi is None and meta.arxiv_id is None


def test_heuristics_skip_affiliation_and_email_lines():
    data = paper_pdf(
        authors="Carol King and Dan Brown",
        extra_first_page=[("University of Testing, Department of Widgets", 10, False), ("carol@example.org", 10, False)],
    )
    assert extract_metadata(extract(data)).authors == ["Carol King", "Dan Brown"]


def test_heuristics_find_doi_and_arxiv_id_and_year():
    data = paper_pdf(extra_first_page=[("arXiv:2103.01234v2 [cs.LG] doi: 10.1145/12345.67890.", 9, False)])
    meta = extract_metadata(extract(data))
    assert meta.arxiv_id == "2103.01234"
    assert meta.year == 2021  # from the arXiv id
    assert meta.doi == "10.1145/12345.67890"  # trailing punctuation dropped
    assert meta.url == "https://arxiv.org/abs/2103.01234"


def test_year_is_latest_plausible_year_on_the_first_page():
    next_year = date.today().year + 1
    data = paper_pdf(extra_first_page=[(f"Published 2019, revised 2020, embargo until {next_year + 1}.", 9, False)])
    assert extract_metadata(extract(data)).year == 2020  # two years ahead is not a publication year
    data = paper_pdf(extra_first_page=[(f"Published 2019, to appear {next_year}.", 9, False)])
    assert extract_metadata(extract(data)).year == next_year  # next year is (forthcoming papers)


def test_embedded_title_is_used_only_when_the_page_confirms_it():
    body = [("Some Generic Heading Text Here", 10, False), ("Body text of the paper. " * 5, 10, False)]
    trusted = build_pdf([body], metadata={"title": "Some Generic Heading Text Here", "author": "Eve Adams"})
    meta = extract_metadata(extract(trusted))
    assert meta.title == "Some Generic Heading Text Here"
    assert meta.authors == ["Eve Adams"]  # falls back to the embedded author field

    junk = build_pdf([body], metadata={"title": "Microsoft Word - draft3.docx"})
    assert extract_metadata(extract(junk)).title is None


@pytest.mark.parametrize(
    "authors, expected",
    [
        ("Ann Lee*, Bo Kim\u2020 and Cy Dee1 & Di Fox", ["Ann Lee", "Bo Kim", "Cy Dee", "Di Fox"]),  # footnote marks
        ("Ludwig van Beethoven; J. R. R. Tolkien", ["Ludwig van Beethoven", "J. R. R. Tolkien"]),  # particles, initials
        # single words, lowercase phrases, digits and over-long runs are not names
        ("Madonna, deep learning methods, Table 3 Results, A B C D E F, Ada Lovelace", ["Ada Lovelace"]),
    ],
)
def test_author_names_are_recognised_on_the_first_page(authors, expected):
    meta = extract_metadata(extract(paper_pdf(authors=authors, html=True)))
    assert meta.authors == expected


def test_footnote_marks_are_removed_from_a_heuristic_title():
    meta = extract_metadata(extract(paper_pdf(title="Robust Widgets*\u2020", html=True)))
    assert meta.title == "Robust Widgets"


def test_too_short_title_is_not_used():
    meta = extract_metadata(extract(paper_pdf(title="Short")))
    assert meta.title is None  # the upload then falls back to the filename


# ----------------------------------------------------------------------- LLM pass


def heuristic_meta(**kw) -> ExtractedMetadata:
    base = dict(title="Heuristic Title Guess", authors=["Wrong Name"], year=2018, abstract=None)
    base.update(kw)
    return ExtractedMetadata(**base)


def test_llm_metadata_replaces_title_and_authors_and_fills_abstract():
    provider = FakeLLM(responses=[{"title": "The Real Title Of It", "authors": ["Ann Lee", " Ann  Lee ", "Bo Kim"],
                                    "year": 2020, "abstract": "An  abstract."}])
    meta = llm_metadata(provider, extract(paper_pdf()), heuristic_meta())
    assert meta.title == "The Real Title Of It"
    assert meta.authors == ["Ann Lee", "Bo Kim"]  # de-duplicated and whitespace-normalised
    assert meta.year == 2020
    assert meta.abstract == "An abstract."
    assert meta.source == "llm"
    call = provider.calls[0]
    assert "abstract" in call["schema"]["properties"]  # asked for because the heuristics had none
    assert "<page>" in call["prompt"] and PAPER_TITLE in call["prompt"]


def test_llm_metadata_keeps_exact_fields_and_rejects_bad_values():
    provider = FakeLLM(responses=[{"title": "x", "authors": [], "year": 3000}])
    original = heuristic_meta(abstract="Known abstract.", arxiv_id="2101.00001", year=2021, doi="10.1/x")
    meta = llm_metadata(provider, extract(paper_pdf()), original)
    assert meta.title == "Heuristic Title Guess"  # "x" is too short to be a title
    assert meta.authors == ["Wrong Name"]  # empty list keeps the heuristics
    assert meta.year == 2021  # out-of-range year rejected
    assert meta.abstract == "Known abstract."
    assert meta.doi == "10.1/x"
    assert meta.source == "pdf"  # nothing changed
    assert "abstract" not in provider.calls[0]["schema"]["properties"]


@pytest.mark.parametrize(
    "llm_year, kept",
    [(1900, True), (1899, False), (date.today().year + 1, True), (date.today().year + 2, False), (0, False)],
)
def test_llm_year_must_be_plausible(llm_year, kept):
    provider = FakeLLM(responses=[{"title": "Heuristic Title Guess", "authors": ["Wrong Name"], "year": llm_year}])
    meta = llm_metadata(provider, extract(paper_pdf()), heuristic_meta(year=2018, abstract="Known."))
    assert meta.year == (llm_year if kept else 2018)


@pytest.mark.parametrize("length, kept", [(7, False), (8, True), (300, True), (301, False)])
def test_llm_title_length_limits(length, kept):
    title = "T" + "x" * (length - 1)
    provider = FakeLLM(responses=[{"title": title, "authors": ["Wrong Name"], "year": 2018}])
    meta = llm_metadata(provider, extract(paper_pdf()), heuristic_meta(abstract="Known."))
    assert meta.title == (title if kept else "Heuristic Title Guess")


def test_llm_metadata_does_not_override_arxiv_year():
    provider = FakeLLM(responses=[{"title": "Heuristic Title Guess", "authors": ["Wrong Name"], "year": 2019}])
    meta = llm_metadata(provider, extract(paper_pdf()), heuristic_meta(year=2021, arxiv_id="2101.00001"))
    assert meta.year == 2021


def test_llm_metadata_propagates_llm_errors():
    with pytest.raises(LLMError):
        llm_metadata(FakeLLM(responses=[LLMError("boom")]), extract(paper_pdf()), heuristic_meta())


# ----------------------------------------------------------------------- lookups


S2_RECORD = {
    "paperId": "s2-xyz",
    "title": "Deep Widgets for Robust Testing",
    "authors": [{"name": "Alice Smith"}, {"name": "Bob Jones"}],
    "year": 2022,
    "abstract": "Official abstract.",
    "url": "https://www.semanticscholar.org/paper/s2-xyz",
    "externalIds": {"DOI": "10.9/WIDGETS", "ArXiv": "2201.00001"},
}


def recorder(routes):
    """A handler answering by URL substring, recording the requests it saw."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        for needle, response in routes:
            if needle in str(request.url):
                if isinstance(response, Exception):
                    raise response
                return response
        return httpx.Response(404)

    return handler, seen


def test_semantic_scholar_lookup_by_doi_is_exact():
    handler, seen = recorder([("/paper/DOI:10.9/widgets", json_response(S2_RECORD))])
    meta = lookup_semantic_scholar(mock_client(handler), heuristic_meta(doi="10.9/widgets"), api_key="k")
    assert meta.source == "semantic_scholar"
    assert meta.external_id == "s2-xyz"
    assert meta.title == "Deep Widgets for Robust Testing"
    assert meta.authors == ["Alice Smith", "Bob Jones"]
    assert meta.doi == "10.9/WIDGETS"
    assert meta.arxiv_id == "2201.00001"
    assert meta.external_source == "s2"
    assert len(seen) == 1


def test_semantic_scholar_lookup_tries_arxiv_id_after_doi_404():
    handler, seen = recorder([("/paper/arXiv:2201.00001", json_response(S2_RECORD))])
    meta = lookup_semantic_scholar(mock_client(handler), heuristic_meta(doi="10.0/unknown", arxiv_id="2201.00001"))
    assert meta.source == "semantic_scholar"
    assert [("DOI:" in u, "arXiv:" in u) for u in seen] == [(True, False), (False, True)]


# Candidate titles around the 0.9 similarity threshold for "Deep widgets for robust testing":
# a title search only finds the most similar paper, so a near miss must not be taken as a match.
TITLE_CANDIDATES = [
    ("Deep Widgets For Robust Testing.", True),  # same title, case and punctuation aside (1.0)
    ("Deep widgets for robust testers", True),  # ~0.90
    ("Deep widgets for robust training", False),  # ~0.89: a different paper
    ("Shallow widgets for robust testing", False),  # ~0.83
    ("Widgets for robust software testing", False),  # ~0.79
    ("Something Entirely Different", False),
]


@pytest.mark.parametrize("candidate, accepted", TITLE_CANDIDATES)
def test_semantic_scholar_title_match_needs_a_near_identical_title(candidate, accepted):
    reply = json_response({"data": [{**S2_RECORD, "title": candidate}]})
    handler, _ = recorder([("search/match", reply)])
    original = heuristic_meta(title="Deep widgets for robust testing")
    meta = lookup_semantic_scholar(mock_client(handler), original)
    if accepted:
        assert meta.source == "semantic_scholar_title"
        assert meta.external_id == "s2-xyz"
    else:
        assert meta is original


@pytest.mark.parametrize("failure", [httpx.Response(429), httpx.Response(500), httpx.ConnectError("offline")])
def test_semantic_scholar_failures_leave_metadata_unchanged(failure):
    handler, _ = recorder([("semanticscholar", failure)])
    original = heuristic_meta(doi="10.1/x")
    assert lookup_semantic_scholar(mock_client(handler), original) is original


def test_arxiv_lookup_by_id_and_by_title():
    feed = arxiv_feed([{"id": "2201.00001v3", "title": "Deep Widgets for Robust Testing", "authors": ["Alice Smith"],
                        "published": "2022-01-01T00:00:00Z"}])
    handler, seen = recorder([("export.arxiv.org", httpx.Response(200, text=feed))])
    by_id = lookup_arxiv(mock_client(handler), heuristic_meta(arxiv_id="2201.00001"))
    assert by_id.source == "arxiv"
    assert by_id.external_id == "2201.00001"
    assert by_id.external_source == "arxiv"
    assert "id_list=2201.00001" in seen[0]

    by_title = lookup_arxiv(mock_client(handler), heuristic_meta(title="Deep widgets for robust testing"))
    assert by_title.source == "arxiv_title"
    assert by_title.year == 2022


@pytest.mark.parametrize("candidate, accepted", TITLE_CANDIDATES)
def test_arxiv_title_lookup_needs_a_near_identical_title(candidate, accepted):
    feed = arxiv_feed([{"id": "1111.11111", "title": candidate}])
    handler, _ = recorder([("export.arxiv.org", httpx.Response(200, text=feed))])
    original = heuristic_meta(title="Deep widgets for robust testing")
    meta = lookup_arxiv(mock_client(handler), original)
    assert (meta.source == "arxiv_title") if accepted else (meta is original)


def test_arxiv_lookup_picks_the_most_similar_candidate():
    feed = arxiv_feed([{"id": "1111.11111", "title": "Deep widgets for robust training"},
                       {"id": "2222.22222", "title": "Deep Widgets for Robust Testing"}])
    handler, _ = recorder([("export.arxiv.org", httpx.Response(200, text=feed))])
    meta = lookup_arxiv(mock_client(handler), heuristic_meta(title="Deep widgets for robust testing"))
    assert meta.external_id == "2222.22222"


def test_arxiv_lookup_survives_malformed_xml():
    handler, _ = recorder([("export.arxiv.org", httpx.Response(200, text="<not xml"))])
    original = heuristic_meta(title="Deep widgets for robust testing")
    assert lookup_arxiv(mock_client(handler), original) is original


def test_lookup_falls_back_from_semantic_scholar_to_arxiv():
    feed = arxiv_feed([{"id": "2201.00001", "title": "Deep Widgets for Robust Testing"}])
    handler, seen = recorder([("semanticscholar", httpx.Response(429)), ("export.arxiv.org", httpx.Response(200, text=feed))])
    meta = lookup(mock_client(handler), heuristic_meta(arxiv_id="2201.00001"))
    assert meta.source == "arxiv"
    assert any("semanticscholar" in u for u in seen) and any("arxiv.org" in u for u in seen)


def test_as_paper_fields_maps_source_to_metadata_source():
    fields = ExtractedMetadata(title="T", source="semantic_scholar", arxiv_id="1").as_paper_fields()
    assert fields["metadata_source"] == "semantic_scholar"
    assert "arxiv_id" not in fields and "source" not in fields
