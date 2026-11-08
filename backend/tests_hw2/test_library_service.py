"""Unit tests for services/library.py: normalisation, duplicate detection, saving, editing, listing."""

import pytest

from app.models import Paper
from app.schemas import PaperCreate, PaperUpdate
from app.services import library
from app.services.library import DuplicateIndex, normalize_doi, normalize_title

from tests_hw2.helpers import add_paper

# ----------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Attention Is All You Need", "attention is all you need"),
        ("Attention is all you need!", "attention is all you need"),
        ("  Attention,   is: all -- you need. ", "attention is all you need"),
        ("Café Résumé Naïve", "cafe resume naive"),
        ("snake_case_title", "snake case title"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize_title_ignores_case_punctuation_and_accents(title, expected):
    assert normalize_title(title) == expected


@pytest.mark.parametrize(
    "doi, expected",
    [
        ("10.1234/ABC.def", "10.1234/abc.def"),
        ("https://doi.org/10.1234/X", "10.1234/x"),
        ("http://dx.doi.org/10.1234/X", "10.1234/x"),
        ("DOI:10.1234/X", "10.1234/x"),
        ("  10.1234/x  ", "10.1234/x"),
        ("", None),
        (None, None),
        ("https://doi.org/", None),
    ],
)
def test_normalize_doi_strips_prefixes_and_lowercases(doi, expected):
    assert normalize_doi(doi) == expected


@pytest.mark.xfail(
    strict=True,
    reason="SUSPECTED BUG: normalize_doi strips whitespace before removing the 'doi:' prefix, so the "
    "common printed form 'doi: 10.x/y' keeps a leading space and no longer matches the same DOI.",
)
def test_normalize_doi_handles_space_after_doi_prefix():
    assert normalize_doi("doi: 10.1234/ABC") == "10.1234/abc"


# ----------------------------------------------------------------------- DuplicateIndex


def test_duplicate_index_matches_by_doi_external_id_then_title(session):
    a = add_paper(session, title="Paper A", doi="10.1/a")
    b = add_paper(session, title="Paper B", source="arxiv", external_id="2101.00001")
    c = add_paper(session, title="Paper C: The Sequel")
    index = DuplicateIndex(session)

    assert index.match(PaperCreate(source="s2", title="Other", doi="https://doi.org/10.1/A")) == a.id
    assert index.match(PaperCreate(source="arxiv", title="Other", external_id="2101.00001")) == b.id
    # same external id but another source is a different paper
    assert index.match(PaperCreate(source="s2", title="Other", external_id="2101.00001")) is None
    assert index.match(PaperCreate(source="s2", title="paper c the sequel")) == c.id
    assert index.match(PaperCreate(source="s2", title="Completely new")) is None


def test_duplicate_index_exclude_id_skips_the_paper_itself(session):
    a = add_paper(session, title="Only Paper")
    assert DuplicateIndex(session).match(a) == a.id
    assert DuplicateIndex(session, exclude_id=a.id).match(a) is None


def test_punctuation_only_titles_never_match_each_other(session):
    add_paper(session, title="???")
    assert DuplicateIndex(session).match(PaperCreate(source="s2", title="!!!")) is None


# ----------------------------------------------------------------------- save_paper


def test_save_paper_creates_and_normalises_doi(session):
    paper, created = library.save_paper(
        session, PaperCreate(source="s2", title="New Paper", doi="https://doi.org/10.5/XYZ", authors=["A B"])
    )
    assert created is True
    assert paper.id is not None
    assert paper.doi == "10.5/xyz"
    assert paper.status == "no_pdf"
    assert session.get(Paper, paper.id).authors == ["A B"]


def test_save_paper_returns_existing_for_duplicate(session):
    first, _ = library.save_paper(session, PaperCreate(source="s2", title="Same Title"))
    again, created = library.save_paper(session, PaperCreate(source="arxiv", title="same title!"))
    assert created is False
    assert again.id == first.id
    assert session.query(Paper).count() == 1


# ----------------------------------------------------------------------- update_paper


def test_update_paper_applies_and_cleans_edits(session):
    paper = add_paper(session, title="Old")
    library.update_paper(
        session,
        paper,
        PaperUpdate(title="  New Title  ", authors=[" Ann ", "", "  "], doi="doi:10.9/ABC", abstract="   ", url=" u "),
    )
    session.refresh(paper)
    assert paper.title == "New Title"
    assert paper.authors == ["Ann"]
    assert paper.doi == "10.9/abc"
    assert paper.abstract is None
    assert paper.url == "u"
    assert paper.metadata_source == "edited"


def test_update_paper_keeps_title_when_blank(session):
    paper = add_paper(session, title="Keep Me")
    library.update_paper(session, paper, PaperUpdate(title="   "))
    session.refresh(paper)
    assert paper.title == "Keep Me"
    assert paper.metadata_source is None  # nothing actually changed


def test_update_paper_without_changes_does_not_mark_edited(session):
    paper = add_paper(session, metadata_source="pdf")
    library.update_paper(session, paper, PaperUpdate())
    assert paper.metadata_source == "pdf"


def test_update_paper_rejects_edit_that_duplicates_another_paper(session):
    other = add_paper(session, title="Taken Title", doi="10.1/taken")
    paper = add_paper(session, title="Mine")
    with pytest.raises(library.DuplicatePaper) as info:
        library.update_paper(session, paper, PaperUpdate(doi="10.1/TAKEN"))
    assert info.value.paper_id == other.id
    session.refresh(paper)
    assert paper.doi is None  # rolled back
    assert paper.title == "Mine"


# ----------------------------------------------------------------------- list_papers


@pytest.fixture
def library_papers(session):
    return [
        add_paper(session, title="Graph Networks", authors=["José Pérez"], year=2018, source="s2"),
        add_paper(session, title="Transformers at 100% scale", year=2021, source="arxiv", abstract="Big models."),
        add_paper(session, title="Upload Study", year=None, source="upload", abstract="About graph theory."),
    ]


def test_list_papers_newest_first(session, library_papers):
    assert [p.title for p in library.list_papers(session)] == [
        "Upload Study",
        "Transformers at 100% scale",
        "Graph Networks",
    ]


def test_list_papers_text_search_covers_title_abstract_and_authors(session, library_papers):
    titles = lambda q: {p.title for p in library.list_papers(session, q=q)}  # noqa: E731
    assert titles("graph") == {"Graph Networks", "Upload Study"}
    assert titles("BIG MODELS") == {"Transformers at 100% scale"}
    assert titles("José") == {"Graph Networks"}
    assert titles("   ") == {p.title for p in library_papers}


def test_list_papers_escapes_like_wildcards(session, library_papers):
    assert [p.title for p in library.list_papers(session, q="100%")] == ["Transformers at 100% scale"]
    # "%" and "_" are literal characters, not LIKE wildcards
    assert [p.title for p in library.list_papers(session, q="%")] == ["Transformers at 100% scale"]
    assert library.list_papers(session, q="_") == []


def test_list_papers_filters_by_source(session, library_papers):
    assert [p.title for p in library.list_papers(session, source="arxiv")] == ["Transformers at 100% scale"]
    assert library.list_papers(session, source="nowhere") == []


@pytest.mark.parametrize(
    "year_from, year_to, expected",
    [
        (2018, None, ["Transformers at 100% scale", "Graph Networks"]),  # lower bound is inclusive
        (2019, None, ["Transformers at 100% scale"]),
        (None, 2021, ["Transformers at 100% scale", "Graph Networks"]),  # upper bound is inclusive
        (None, 2020, ["Graph Networks"]),
        (2021, 2021, ["Transformers at 100% scale"]),  # a single year
        (2018, 2018, ["Graph Networks"]),
        (2022, 2010, []),  # an inverted range matches nothing
    ],
)
def test_list_papers_year_range_is_inclusive_and_skips_undated_papers(session, library_papers, year_from, year_to,
                                                                       expected):
    found = [p.title for p in library.list_papers(session, year_from=year_from, year_to=year_to)]
    assert found == expected  # "Upload Study" has no year, so any year filter leaves it out
