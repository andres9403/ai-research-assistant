"""The LLM pass over an upload's first page (between the heuristics and the lookups)."""

from app.services import llm
from app.services.metadata import ExtractedMetadata, extract_metadata, llm_metadata
from app.services.pdf import extract
from tests.conftest import FakeLLM
from tests.pdf_factory import ABSTRACT, AUTHORS, TITLE, make_paper_pdf
from tests.test_pdf_api import upload

FIXED_AUTHORS = ["Ana M. López", "Wei Zhang", "John O'Neil", "Priya Raman"]


def test_upload_uses_the_llm_reading_of_page_one(client, fake_llm, network):
    fake_llm.answers["paper_metadata"] = {"title": TITLE, "authors": FIXED_AUTHORS, "year": 2023}

    paper = upload(client).json()

    assert paper["metadata_source"] == "llm"
    assert paper["authors"] == FIXED_AUTHORS
    assert paper["title"] == TITLE
    assert paper["year"] == 2024  # the arXiv id is exact, so it beats the LLM
    assert paper["abstract"] == ABSTRACT  # found in the text, so not asked for

    [call] = fake_llm.calls
    assert "abstract" not in call["schema"]["properties"]
    # Only page one is sent: the stamp, title block and opening text, not later pages.
    assert call["prompt"].startswith("<page>\narXiv:2401.01234v2")
    assert "Sparse Expert Routing" in call["prompt"]
    assert "Reference work number" not in call["prompt"]
    assert "accuracy 2" not in call["prompt"]
    assert "Preprint. Under review." not in call["prompt"]  # running heads are cleaned out


def test_llm_title_drives_the_title_lookup(client, fake_llm, network):
    fake_llm.answers["paper_metadata"] = {"title": "Sparse Expert Routing, Revisited", "authors": AUTHORS, "year": 2022}

    paper = upload(client, make_paper_pdf(arxiv_stamp=None)).json()

    assert paper["metadata_source"] == "llm"
    assert paper["title"] == "Sparse Expert Routing, Revisited"
    assert paper["year"] == 2022
    match = [r for r in network.requests if r.url.path.endswith("/search/match")]
    assert match[0].url.params["query"] == "Sparse Expert Routing, Revisited"


def test_upload_survives_an_llm_failure(client, fake_llm):
    fake_llm.answers["paper_metadata"] = llm.LLMError("Anthropic is rate-limiting requests.")
    resp = upload(client)
    assert resp.status_code == 201
    assert resp.json()["metadata_source"] == "pdf"
    assert resp.json()["authors"] == AUTHORS


def test_upload_without_an_llm_skips_the_pass(client):
    resp = upload(client)
    assert resp.status_code == 201
    assert resp.json()["metadata_source"] == "pdf"


def test_missing_abstract_is_requested_and_bad_values_are_ignored():
    pdf = extract(make_paper_pdf(arxiv_stamp=None))
    meta = extract_metadata(pdf)
    meta.abstract = None
    fake = FakeLLM()
    fake.answers["paper_metadata"] = {
        "title": "x",  # too short to be a title: the heuristic one stays
        "authors": ["  Wei   Zhang ", "", "Wei Zhang", "N" * 150],
        "year": 0,  # unknown: the heuristic year stays
        "abstract": "  An abstract\nfrom page one. ",
    }

    improved = llm_metadata(fake, pdf, meta)

    assert "abstract" in fake.calls[0]["schema"]["required"]
    assert improved.title == meta.title
    assert improved.authors == ["Wei Zhang"]
    assert improved.year == meta.year
    assert improved.abstract == "An abstract from page one."
    assert improved.source == "llm"


def test_unchanged_reading_keeps_the_pdf_source():
    pdf = extract(make_paper_pdf())
    meta = extract_metadata(pdf)
    fake = FakeLLM()
    fake.answers["paper_metadata"] = {"title": meta.title, "authors": meta.authors, "year": meta.year}
    assert llm_metadata(fake, pdf, meta).source == "pdf"


def test_blank_page_skips_the_call():
    pdf = extract(make_paper_pdf())
    pdf.lines = [line for line in pdf.lines if line.page != 0]
    pdf.rotated_text = ""
    fake = FakeLLM()
    meta = ExtractedMetadata(title="Kept")
    assert llm_metadata(fake, pdf, meta) is meta
    assert fake.calls == []
