import pytest

from app.services import pdf
from app.services.pdf import INLINE_ABSTRACT, Line, PdfError, extract, heading_name
from tests.pdf_factory import encrypted_pdf, make_ieee_pdf, make_paper_pdf, make_scanned_pdf


@pytest.fixture(scope="module")
def paper():
    return extract(make_paper_pdf())


def test_sections_follow_the_paper_structure(paper):
    assert [s.title for s in paper.sections] == [
        "Front matter", "Abstract", "Introduction", "Method", "Results", "References",
        "Additional Proofs",
    ]
    assert paper.page_count == 4
    intro = paper.sections[2]
    assert (intro.page_start, intro.page_end) == (1, 1)


def test_running_heads_and_page_numbers_are_removed(paper):
    texts = [line.text for line in paper.lines]
    assert "Preprint. Under review." not in texts
    assert not any(t.strip().isdigit() for t in texts)


def test_rotated_arxiv_stamp_is_kept_out_of_the_body(paper):
    assert "arXiv:2401.01234v2" in paper.rotated_text
    assert not any("arXiv:2401" in line.text for line in paper.lines)


def test_line_break_hyphenation_is_rejoined(paper):
    intro = next(s for s in paper.sections if s.title == "Introduction")
    assert "large transformer network" in intro.text
    assert "trans-" not in intro.text
    assert "Mixture-of-experts" in intro.text  # real hyphens stay


def test_chunks_skip_front_matter_and_references(paper):
    sections = {c.section for c in paper.chunks}
    assert sections == {"Abstract", "Introduction", "Method", "Results", "Additional Proofs"}
    assert not any("Reference work number" in c.text for c in paper.chunks)
    assert not any("@example.edu" in c.text for c in paper.chunks)


def test_chunks_are_bounded_and_located(paper):
    for chunk in paper.chunks:
        words = len(chunk.text.split())
        assert words <= pdf.CHUNK_TARGET_WORDS + pdf.CHUNK_MIN_WORDS
        assert 1 <= chunk.page_start <= chunk.page_end <= paper.page_count
        assert chunk.n_tokens == pdf.estimate_tokens(chunk.text)
    # Every body sentence made it into some chunk exactly once.
    body = " ".join(c.text for c in paper.chunks if c.section == "Method")
    assert body.count("Sentence 13 explains how the router 2") == 1


def test_table_header_word_is_not_a_heading(paper):
    results = next(s for s in paper.sections if s.title == "Results")
    assert results.text.startswith("Model")


def test_roman_numeral_headings_and_inline_abstract():
    doc = extract(make_ieee_pdf())
    assert [s.title for s in doc.sections] == ["Front matter", "Abstract", "Introduction", "Related Work"]
    assert doc.sections[1].text.startswith("We predict molecular properties")


@pytest.mark.parametrize(
    "text", ["Abstract—We study X", "Abstract: We study X", "ABSTRACT. We study X"]
)
def test_inline_abstract_variants(text):
    assert INLINE_ABSTRACT.match(text).group(1) == "We study X"


def line(text, size=10.0, bold=False):
    return Line(text=text, page=0, size=size, bold=bold, block=0, y0=0, y1=10, page_height=800)


@pytest.mark.parametrize(
    "candidate, expected",
    [
        (line("2.1 Training Details", bold=True), "Training Details"),
        (line("4 Our Novel Router", size=12), "Our Novel Router"),
        (line("3 Experiments"), "Experiments"),  # numbered and known: no emphasis needed
        (line("REFERENCES"), "References"),
        (line("Appendix B: Extra Tables", bold=True), "Appendix B: Extra Tables"),
        (line("Results"), None),  # ambiguous word at body size, e.g. a table header
        (line("Results", size=12), "Results"),
        (line("3 We then train the model for ten epochs."), None),  # numbered list item
        (line("12. Smith, J. A study of things"), None),  # reference entry
        (line("A Proof of the Main Theorem That Goes On and On Far Too Long To Be A Heading", bold=True), None),
    ],
)
def test_heading_detection(candidate, expected):
    assert heading_name(candidate, body_size=10.0) == expected


def test_scanned_pdf_has_no_text():
    doc = extract(make_scanned_pdf())
    assert doc.page_count == 1
    assert not doc.has_text and doc.chunks == []


@pytest.mark.parametrize(
    "data, message",
    [
        (b"<html>not a pdf</html>", "not a PDF"),
        (b"%PDF-1.7\nthis is not really a pdf", "could not be read"),
        (encrypted_pdf(), "password"),
    ],
    ids=["html", "corrupt", "encrypted"],
)
def test_unreadable_pdfs_are_rejected(data, message):
    with pytest.raises(PdfError, match=message):
        extract(data)


def test_long_paragraphs_are_split_at_sentences():
    text = " ".join(f"This is sentence number {i} of a long paragraph." for i in range(100))
    pieces = pdf._split_long(text, 120)
    assert all(len(p.split()) <= 120 for p in pieces)
    assert " ".join(pieces) == text
    assert all(p.endswith(".") for p in pieces)
