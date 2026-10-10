"""Local PDF text pipeline: extract → clean → split into sections → chunk.

Everything here runs locally with PyMuPDF, so no PDF text leaves the machine
until a later milestone sends selected chunks to the LLM.
"""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

MAX_PDF_BYTES = 50 * 1024 * 1024

# Chunks are paragraph-aligned and sized for retrieval: ~250 words ≈ 330 tokens.
CHUNK_TARGET_WORDS = 250
CHUNK_MIN_WORDS = 60

FRONT_MATTER = "Front matter"
FULL_TEXT = "Full text"

KNOWN_HEADINGS = {
    "abstract", "introduction", "background", "related work", "related works",
    "prior work", "preliminaries", "method", "methods", "methodology",
    "approach", "proposed method", "model", "models", "materials and methods",
    "experiments", "experimental setup", "experimental results", "evaluation",
    "results", "results and discussion", "analysis", "discussion",
    "conclusion", "conclusions", "conclusion and future work", "future work",
    "limitations", "broader impact", "ethics statement", "acknowledgments",
    "acknowledgements", "acknowledgment", "acknowledgement", "references",
    "bibliography", "appendix", "appendices", "supplementary material",
}
AMBIGUOUS_HEADINGS = {
    "method", "methods", "model", "models", "approach", "analysis", "results",
    "evaluation", "experiments", "background", "discussion",
}
# Sections that are never chunked: they cost tokens and rarely answer questions.
SKIPPED_SECTIONS = {"references", "bibliography", "acknowledgments", "acknowledgements",
                    "acknowledgment", "acknowledgement"}

# "1 Introduction", "2.3. Training", "IV. RESULTS", "A Proofs"
NUMBERED_HEADING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVX]{1,5}\.|[A-H]\.?)\s+(\S.*)$")
# "Abstract—We propose …" / "Abstract. We …" (IEEE and LNCS styles)
INLINE_ABSTRACT = re.compile(r"^abstract\s*[—–:.-]\s*(\S.*)$", re.I)
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?$", re.I)


class PdfError(ValueError):
    """The upload isn't a PDF we can read."""


@dataclass
class Line:
    text: str
    page: int  # 0-based
    size: float
    bold: bool
    block: int  # PyMuPDF block number on the page; a new block starts a paragraph
    y0: float
    y1: float
    page_height: float


@dataclass
class Section:
    title: str
    page_start: int  # 1-based
    page_end: int
    paragraphs: list[tuple[str, int]] = field(default_factory=list)  # (text, 1-based page)

    @property
    def text(self) -> str:
        return "\n\n".join(p for p, _ in self.paragraphs)


@dataclass
class ChunkData:
    section: str
    text: str
    page_start: int
    page_end: int

    @property
    def n_tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class ExtractedPdf:
    page_count: int
    lines: list[Line]  # cleaned body lines, in reading order
    sections: list[Section]
    chunks: list[ChunkData]
    pdf_metadata: dict
    rotated_text: str  # e.g. the arXiv id stamped up the left margin
    body_size: float

    @property
    def has_text(self) -> bool:
        return bool(self.lines)


# Claude's tokenizer averages about 2.8 characters per token on paper text and
# OpenAI's about 4, so 3 keeps every token budget close to a real ceiling for both.
CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def open_pdf(data: bytes) -> pymupdf.Document:
    if len(data) > MAX_PDF_BYTES:
        raise PdfError(f"PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if b"%PDF-" not in data[:1024]:
        raise PdfError("File is not a PDF")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several unrelated types
        raise PdfError("PDF could not be read") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfError("PDF is password-protected")
    return doc


def extract(data: bytes) -> ExtractedPdf:
    with open_pdf(data) as doc:
        lines, rotated = _read_lines(doc)
        page_count = doc.page_count
        pdf_metadata = dict(doc.metadata or {})

    lines = remove_headers_and_footers(lines, page_count)
    body_size = _body_font_size(lines)
    sections = split_sections(lines, body_size)
    return ExtractedPdf(
        page_count=page_count,
        lines=lines,
        sections=sections,
        chunks=chunk_sections(sections),
        pdf_metadata=pdf_metadata,
        rotated_text=rotated,
        body_size=body_size,
    )


def _normalize(text: str) -> str:
    # NFKC expands ligatures (ﬁ → fi) and odd spaces from LaTeX fonts.
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    return " ".join(text.split())


def _read_lines(doc: pymupdf.Document) -> tuple[list[Line], str]:
    lines: list[Line] = []
    rotated: list[str] = []
    for page_no, page in enumerate(doc):
        height = page.rect.height
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for raw in block["lines"]:
                spans = [s for s in raw["spans"] if s["text"].strip()]
                text = _normalize("".join(s["text"] for s in raw["spans"]))
                if not text or not spans:
                    continue
                dx, dy = raw["dir"]
                if abs(dy) > 0.1 or dx < 0:  # rotated margin text such as arXiv stamps
                    rotated.append(text)
                    continue
                main = max(spans, key=lambda s: len(s["text"].strip()))
                line = Line(
                    text=text,
                    page=page_no,
                    size=round(main["size"], 1),
                    bold=all(_is_bold(s) for s in spans),
                    block=block["number"],
                    y0=raw["bbox"][1],
                    y1=raw["bbox"][3],
                    page_height=height,
                )
                prev = lines[-1] if lines else None
                if prev and prev.page == page_no and prev.block == line.block and _same_row(prev, line):
                    # PyMuPDF splits "1" and "Introduction" into separate lines.
                    prev.text = f"{prev.text} {line.text}"
                    prev.size = max(prev.size, line.size)
                    prev.bold = prev.bold and line.bold
                else:
                    lines.append(line)
    return lines, " ".join(rotated)


def _same_row(a: Line, b: Line) -> bool:
    return abs(a.y0 - b.y0) < 2 and abs(a.y1 - b.y1) < 2


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & pymupdf.TEXT_FONT_BOLD) or "bold" in span["font"].lower()


def remove_headers_and_footers(lines: list[Line], page_count: int) -> list[Line]:
    """Drop page numbers and running heads/feet that repeat across pages."""

    def in_margin(line: Line) -> bool:
        return line.y1 < line.page_height * 0.08 or line.y0 > line.page_height * 0.92

    def key(line: Line) -> str:
        return re.sub(r"\d+", "#", line.text.lower())

    pages_with = Counter()
    for k, page in {(key(l), l.page) for l in lines if in_margin(l)}:
        pages_with[k] += 1
    threshold = max(2, math.ceil(page_count * 0.5))

    return [
        line
        for line in lines
        if not (
            in_margin(line)
            and (PAGE_NUMBER.match(line.text) or pages_with[key(line)] >= threshold)
        )
    ]


def _body_font_size(lines: list[Line]) -> float:
    sizes = Counter()
    for line in lines:
        sizes[line.size] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 0.0


def heading_name(line: Line, body_size: float) -> str | None:
    """Return the section name if `line` is a section heading, else None."""
    text = line.text.strip()
    if len(text) > 90 or len(text.split()) > 12:
        return None
    emphasised = line.bold or line.size >= body_size + 0.9

    if m := NUMBERED_HEADING.match(text):
        name = m.group(1).strip()
        if _is_known(name):
            return _title_case(name)
        # Numbered non-standard headings ("3 Our Dilated Attention") need emphasis,
        # so numbered list items and reference entries aren't mistaken for headings.
        if emphasised and name[0].isupper() and not name.endswith((".", ",", ";")):
            return name
        return None

    if _is_known(text):
        name = text.rstrip(".:")
        # Words like "Model" or "Results" are common table headers; unnumbered,
        # they only count as headings when set in a larger font.
        if name.lower() in AMBIGUOUS_HEADINGS:
            return _title_case(name) if line.size >= body_size + 0.9 else None
        if emphasised or name.isupper():
            return _title_case(name)
    return None


def _is_known(name: str) -> bool:
    name = name.lower().rstrip(".:").strip()
    return name in KNOWN_HEADINGS or bool(re.match(r"^appendix\b", name))


def _title_case(name: str) -> str:
    return name.title() if name.isupper() or name.islower() else name


def split_sections(lines: list[Line], body_size: float) -> list[Section]:
    sections: list[Section] = []
    current = Section(FRONT_MATTER, 1, 1)
    last_key = None  # (page, block) of the previous line, to spot paragraph breaks

    def add_text(text: str, line: Line, new_paragraph: bool):
        page = line.page + 1
        current.page_end = page
        if new_paragraph or not current.paragraphs:
            current.paragraphs.append((text, page))
        else:
            prev, prev_page = current.paragraphs[-1]
            current.paragraphs[-1] = (_join_lines(prev, text), prev_page)

    for line in lines:
        key = (line.page, line.block)
        inline = INLINE_ABSTRACT.match(line.text)
        name = "Abstract" if inline else heading_name(line, body_size)
        if name:
            sections.append(current)
            current = Section(name, line.page + 1, line.page + 1)
            if inline:
                add_text(inline.group(1), line, True)
        else:
            add_text(line.text, line, key != last_key)
        last_key = key
    sections.append(current)

    sections = [s for s in sections if s.paragraphs]
    has_headings = any(s.title != FRONT_MATTER for s in sections)
    if not has_headings:
        for s in sections:
            s.title = FULL_TEXT
    elif sections and sections[0].title == FRONT_MATTER and sections[0].page_start > 2:
        sections[0].title = FULL_TEXT  # headings only start late; this isn't front matter
    return sections


def _join_lines(prev: str, nxt: str) -> str:
    # Rejoin words hyphenated across a line break ("trans-" + "former").
    if re.search(r"[a-z]-$", prev) and nxt[:1].islower():
        return prev[:-1] + nxt
    return f"{prev} {nxt}"


def is_chunkable(section: Section) -> bool:
    return section.title != FRONT_MATTER and section.title.lower() not in SKIPPED_SECTIONS


def chunk_sections(sections: list[Section]) -> list[ChunkData]:
    # Headings after References (appendices) start new sections, so they're kept.
    return [chunk for s in sections if is_chunkable(s) for chunk in _chunk_section(s)]


def _chunk_section(section: Section) -> list[ChunkData]:
    pieces = [
        (piece, page)
        for text, page in section.paragraphs
        for piece in _split_long(text, CHUNK_TARGET_WORDS)
    ]
    chunks: list[ChunkData] = []
    buf: list[str] = []
    words = 0
    start = end = section.page_start

    for text, page in pieces:
        n = len(text.split())
        if buf and words + n > CHUNK_TARGET_WORDS:
            chunks.append(ChunkData(section.title, "\n\n".join(buf), start, end))
            buf, words = [], 0
        if not buf:
            start = page
        buf.append(text)
        words += n
        end = page

    if buf:
        tail = ChunkData(section.title, "\n\n".join(buf), start, end)
        if chunks and words < CHUNK_MIN_WORDS:
            last = chunks[-1]
            chunks[-1] = ChunkData(section.title, f"{last.text}\n\n{tail.text}", last.page_start, end)
        else:
            chunks.append(tail)
    return chunks


def _split_long(text: str, max_words: int) -> list[str]:
    """Split a paragraph longer than `max_words` at sentence boundaries."""
    if len(text.split()) <= max_words:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text)
    out: list[str] = []
    buf: list[str] = []
    words = 0
    for sentence in sentences:
        n = len(sentence.split())
        if buf and words + n > max_words:
            out.append(" ".join(buf))
            buf, words = [], 0
        buf.append(sentence)
        words += n
    if buf:
        out.append(" ".join(buf))
    # A single run-on "sentence" can still be too long; cut it by words.
    final: list[str] = []
    for piece in out:
        tokens = piece.split()
        final.extend(" ".join(tokens[i : i + max_words]) for i in range(0, len(tokens), max_words))
    return final
