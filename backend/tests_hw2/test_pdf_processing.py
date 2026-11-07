"""Tests for services/pdf.py: validation, text extraction, section splitting and chunking."""

import pymupdf
import pytest

from app.services import pdf
from app.services.pdf import (
    CHUNK_TARGET_WORDS,
    FRONT_MATTER,
    FULL_TEXT,
    Line,
    PdfError,
    Section,
    chunk_sections,
    estimate_tokens,
    extract,
    heading_name,
    open_pdf,
    remove_headers_and_footers,
    split_sections,
)

from tests_hw2.helpers import build_pdf, encrypted_pdf, paper_pdf, scanned_like_pdf, sentence_block


def line(text, page=0, size=10.0, bold=False, block=0, y0=300.0, y1=310.0, height=800.0):
    return Line(text=text, page=page, size=size, bold=bold, block=block, y0=y0, y1=y1, page_height=height)


# ----------------------------------------------------------------------- open_pdf


def test_open_pdf_rejects_non_pdf_bytes():
    with pytest.raises(PdfError, match="not a PDF"):
        open_pdf(b"hello, I am a text file")


def test_open_pdf_rejects_corrupt_pdf():
    with pytest.raises(PdfError, match="could not be read"):
        open_pdf(b"%PDF-1.7\n" + b"\x00garbage" * 10)


def test_open_pdf_rejects_password_protected_pdf():
    with pytest.raises(PdfError, match="password-protected"):
        open_pdf(encrypted_pdf())


def test_open_pdf_rejects_oversized_data(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 100)
    with pytest.raises(PdfError, match="larger than"):
        open_pdf(b"%PDF-" + b"x" * 200)


def test_open_pdf_accepts_a_pdf_header_after_leading_bytes():
    data = paper_pdf()
    doc = open_pdf(b"\n\n" + data)
    assert doc.page_count == 2
    doc.close()


# ----------------------------------------------------------------------- extract


def test_extract_finds_sections_pages_and_chunks():
    result = extract(paper_pdf())
    assert result.page_count == 2
    assert result.has_text
    titles = [s.title for s in result.sections]
    assert titles == [FRONT_MATTER, "Abstract", "Introduction", "Results", "Conclusion", "References"]
    results = next(s for s in result.sections if s.title == "Results")
    assert (results.page_start, results.page_end) == (2, 2)
    # front matter and references are never chunked
    chunk_sections_seen = {c.section for c in result.chunks}
    assert chunk_sections_seen == {"Abstract", "Introduction", "Results", "Conclusion"}
    assert all(c.text.strip() for c in result.chunks)
    assert all(c.n_tokens == estimate_tokens(c.text) for c in result.chunks)
    assert "widgets improve accuracy" in " ".join(c.text for c in result.chunks)


def test_extract_reads_pdf_metadata():
    data = build_pdf([[("Some body text here.", 10, False)]], metadata={"title": "Embedded", "author": "X Y"})
    assert extract(data).pdf_metadata["title"] == "Embedded"


def test_extract_without_text_has_no_lines_or_chunks():
    result = extract(scanned_like_pdf())
    assert result.page_count == 1
    assert result.has_text is False
    assert result.chunks == []
    assert result.sections == []


def test_extract_without_headings_uses_full_text_section():
    data = build_pdf([[(sentence_block("Plain prose without any headings at all.", 30), 10, False)]])
    result = extract(data)
    assert [s.title for s in result.sections] == [FULL_TEXT]
    assert result.chunks and all(c.section == FULL_TEXT for c in result.chunks)


def test_text_normalisation_expands_ligatures_and_collapses_spaces():
    # base-14 PDF fonts can't draw ligature glyphs, so this checks the normaliser directly
    assert pdf._normalize("An eﬃcient ﬁne   method­") == "An efficient fine method"


def test_extract_merges_split_heading_spans_and_ignores_images_and_rotated_stamps():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 600), "arXiv:2103.01234v2 [cs.LG] 4 Mar 2021", fontsize=9, rotate=90)
    page.insert_text((72, 100), "1", fontsize=12, fontname="hebo")  # PyMuPDF reads these as two lines
    page.insert_text((90, 100), "Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 130), "Body text here about widgets.", fontsize=10)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(300, 300, 350, 350), pixmap=pix)
    result = extract(doc.tobytes())
    assert [l.text for l in result.lines] == ["1 Introduction", "Body text here about widgets."]
    assert result.rotated_text == "arXiv:2103.01234v2 [cs.LG] 4 Mar 2021"  # kept apart from the body
    assert [s.title for s in result.sections] == ["Introduction"]
    assert result.sections[0].text == "Body text here about widgets."


def test_inline_abstract_starts_an_abstract_section():
    data = build_pdf([[("Abstract: We propose widgets for testing and evaluate them.", 10, False)]])
    sections = extract(data).sections
    assert sections[0].title == "Abstract"
    assert sections[0].text.startswith("We propose widgets")


# ----------------------------------------------------------------------- headers / footers


def test_repeated_running_heads_and_page_numbers_are_removed():
    lines = []
    for page in range(3):
        lines.append(line("Journal of Widgets, Vol 3", page=page, y0=10, y1=20))  # running head
        lines.append(line(f"Body text on page {page}", page=page))
        lines.append(line(str(page + 1), page=page, y0=780, y1=790))  # page number
    kept = remove_headers_and_footers(lines, page_count=3)
    assert [l.text for l in kept] == ["Body text on page 0", "Body text on page 1", "Body text on page 2"]


def test_single_margin_line_that_does_not_repeat_is_kept():
    lines = [line("A unique footnote in the margin", y0=780, y1=790), line("Body")]
    assert len(remove_headers_and_footers(lines, page_count=4)) == 2


# ----------------------------------------------------------------------- headings


@pytest.mark.parametrize(
    "text, kwargs, expected",
    [
        ("1 Introduction", {}, "Introduction"),
        ("2.3. Related Work", {}, "Related Work"),
        ("IV. RESULTS", {}, "Results"),
        ("INTRODUCTION", {}, "Introduction"),
        ("Conclusion", {"bold": True}, "Conclusion"),
        ("Conclusion", {}, None),  # plain body-size text is not a heading
        ("Results", {"bold": True}, None),  # ambiguous words need a larger font
        ("Results", {"size": 12.0}, "Results"),
        ("3 Our Dilated Attention", {"bold": True}, "Our Dilated Attention"),
        ("3 our dilated attention", {"bold": True}, None),
        ("3 Our Dilated Attention", {}, None),  # numbered non-standard heading needs emphasis
        ("Appendix B: Proofs", {"bold": True}, "Appendix B: Proofs"),
        ("1 " + "word " * 20, {"bold": True}, None),  # too long
    ],
)
def test_heading_name(text, kwargs, expected):
    assert heading_name(line(text, **kwargs), body_size=10.0) == expected


def test_split_sections_starts_new_paragraph_on_new_block_and_joins_hyphenation():
    lines = [
        line("Introduction", bold=True, block=0),
        line("Widgets are trans-", block=1),
        line("formative tools.", block=1),
        line("A second paragraph.", block=2),
    ]
    sections = split_sections(lines, body_size=10.0)
    assert [s.title for s in sections] == ["Introduction"]
    assert [p for p, _ in sections[0].paragraphs] == ["Widgets are transformative tools.", "A second paragraph."]


def test_front_matter_before_an_early_heading_is_not_chunked():
    lines = [line("Title and authors", page=0), line("Introduction", page=1, bold=True, block=1),
             line("Body.", page=1, block=2)]
    sections = split_sections(lines, body_size=10.0)
    assert [s.title for s in sections] == [FRONT_MATTER, "Introduction"]
    assert [c.section for c in chunk_sections(sections)] == ["Introduction"]


@pytest.mark.xfail(
    strict=True,
    reason="SUSPECTED BUG: split_sections means to relabel text before a late first heading as "
    "'Full text' (`sections[0].page_start > 2`), but the front-matter Section is created with "
    "page_start=1 and page_start is never updated, so the branch is dead: pages of body text before "
    "a late heading stay 'Front matter' and are silently dropped from chunking (no Q&A/summary on them).",
)
def test_text_before_a_late_first_heading_is_kept_as_full_text():
    body = " ".join(["word"] * 80)
    lines = [line(f"{body} {page}", page=page, block=page) for page in range(4)]  # pages 1-4 of prose
    lines += [line("Conclusion", page=4, bold=True, block=10), line("Done here.", page=4, block=11)]
    sections = split_sections(lines, body_size=10.0)
    assert sections[0].title == FULL_TEXT
    assert FULL_TEXT in {c.section for c in chunk_sections(sections)}


# ----------------------------------------------------------------------- chunking


def make_section(title, paragraphs, page=1):
    return Section(title, page, page, [(p, page) for p in paragraphs])


def test_chunks_fill_up_to_the_target_size():
    para = " ".join(["word"] * 100)
    chunks = chunk_sections([make_section("Method", [para, para, para, " ".join(["tail"] * 10)])])
    # 100+100 fit in 250 words; a third paragraph would not, so it starts the next chunk
    assert [len(c.text.split()) for c in chunks] == [200, 110]
    assert all(c.section == "Method" for c in chunks)
    assert chunks[1].text.split("\n\n") == [para, " ".join(["tail"] * 10)]  # paragraphs kept apart


def test_short_tail_is_merged_into_the_previous_chunk():
    section = Section("Method", 2, 3, [(" ".join(["body"] * 240), 2), (" ".join(["tail"] * 30), 3)])
    [chunk] = chunk_sections([section])  # a 30-word tail is below CHUNK_MIN_WORDS, so it isn't its own chunk
    assert len(chunk.text.split()) == 270
    assert chunk.text.endswith("tail")
    assert (chunk.page_start, chunk.page_end) == (2, 3)


def test_long_paragraph_is_split_at_sentences_then_words():
    sentences = " ".join(f"Sentence number {i} has exactly seven words." for i in range(100))  # 700 words
    chunks = chunk_sections([make_section("Results", [sentences])])
    assert len(chunks) >= 3
    assert all(len(c.text.split()) <= CHUNK_TARGET_WORDS + pdf.CHUNK_MIN_WORDS for c in chunks)
    assert sum(len(c.text.split()) for c in chunks) == 700

    run_on = " ".join(["x"] * 600)  # no sentence boundaries at all
    pieces = chunk_sections([make_section("Results", [run_on])])
    assert [len(c.text.split()) for c in pieces] == [250, 250, 100]  # a 100-word tail is big enough to stand


def test_chunks_track_page_ranges():
    section = Section("Discussion", 3, 4, [(" ".join(["a"] * 200), 3), (" ".join(["b"] * 30), 4)])
    [chunk] = chunk_sections([section])
    assert (chunk.page_start, chunk.page_end) == (3, 4)


def test_reference_like_sections_are_not_chunked():
    sections = [
        make_section(FRONT_MATTER, ["Title page"]),
        make_section("References", ["[1] A. B. Paper."]),
        make_section("Acknowledgments", ["Thanks."]),
        make_section("Appendix A", ["Extra proofs here."]),
    ]
    assert [c.section for c in chunk_sections(sections)] == ["Appendix A"]


@pytest.mark.parametrize("text, tokens", [("", 0), ("abc", 1), ("abcd", 2), ("x" * 300, 100)])
def test_estimate_tokens(text, tokens):
    assert estimate_tokens(text) == tokens
