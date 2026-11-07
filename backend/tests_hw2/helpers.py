"""Builders shared by the hw2 test suite: synthetic PDFs, fakes for the LLM and the
embedding model, and mock HTTP clients. Nothing here touches the network."""

import hashlib
import json
import re
from dataclasses import dataclass, field

import httpx
import numpy as np
import pymupdf

from app.models import Chunk, Paper
from app.services.llm import LLMError, LLMResult
from app.services.pdf import estimate_tokens

# --------------------------------------------------------------------------- PDFs


def build_pdf(pages: list[list[tuple[str, float, bool]]], metadata: dict | None = None) -> bytes:
    """A PDF whose pages hold (text, font size, bold) paragraphs, stacked top to bottom."""
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page()
        y = 72.0
        for text, size, bold in items:
            rect = pymupdf.Rect(72, y, 540, 760)
            rest = page.insert_textbox(rect, text, fontsize=size, fontname="hebo" if bold else "helv")
            used = (760 - y) - rest if rest >= 0 else 0
            y += used + size * 1.5
    if metadata:
        doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data


def sentence_block(sentence: str, repeat: int) -> str:
    return " ".join([sentence] * repeat)


PAPER_TITLE = "Deep Widgets For Robust Testing"


def paper_pdf(title: str = PAPER_TITLE, authors: str = "Alice Smith, Bob Jones", extra_first_page=()) -> bytes:
    """A small, well-structured two-page paper: title, authors, abstract, intro, results, references."""
    return build_pdf(
        [
            [
                (title, 20, True),
                (authors, 11, False),
                *extra_first_page,
                ("Abstract", 12, True),
                (sentence_block("We study widgets in detail and show they work well in practice.", 6), 10, False),
                ("1 Introduction", 12, True),
                (sentence_block("Widgets matter for software testing at scale.", 25), 10, False),
            ],
            [
                ("2 Results", 12, True),
                (sentence_block("Our results show widgets improve accuracy by ten percent.", 12), 10, False),
                ("3 Conclusion", 12, True),
                (sentence_block("Widgets are useful and we conclude they help testers.", 10), 10, False),
                ("References", 12, True),
                ("[1] Some Body. Some cited paper. 2020.", 10, False),
            ],
        ]
    )


def scanned_like_pdf() -> bytes:
    """A valid PDF with no extractable text (what a scanned paper looks like to PyMuPDF)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    return data


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret", fontsize=12)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.close()
    return data


# --------------------------------------------------------------------------- fakes


class FakeEmbedder:
    """Deterministic bag-of-words vectors, so similar texts get similar unit vectors."""

    dim = 64

    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail = False

    def embed(self, texts: list[str]) -> np.ndarray:
        from app.services.embeddings import EmbeddingError

        if self.fail:
            raise EmbeddingError("The local embedding model failed while reading the paper.")
        self.calls.append(list(texts))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z]+", text.lower()):
                out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = np.linalg.norm(out[row])
            if norm == 0:
                out[row, 0] = 1.0
            else:
                out[row] /= norm
        return out


@dataclass
class FakeLLM:
    """Stands in for an LLM provider. `responses` are returned in order (the last one
    repeats); an Exception in the list is raised instead; a callable gets the call kwargs."""

    name: str = "anthropic"
    model: str = "fake-model"
    responses: list = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    input_tokens: int = 123
    output_tokens: int = 45

    def complete_json(self, *, system, prompt, schema, schema_name, max_tokens) -> LLMResult:
        call = dict(system=system, prompt=prompt, schema=schema, schema_name=schema_name, max_tokens=max_tokens)
        self.calls.append(call)
        if not self.responses:
            raise LLMError("FakeLLM has no response queued")
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(call)
        return LLMResult(data=response, input_tokens=self.input_tokens, output_tokens=self.output_tokens)


SUMMARY_DATA = {
    "tldr": "Widgets help testing.",
    "problem": "Testing is hard.",
    "approach": "We use widgets.",
    "data": "A widget benchmark.",
    "results": "Ten percent better.",
    "limitations": "Only small widgets.",
}


# --------------------------------------------------------------------------- HTTP


def mock_client(handler) -> httpx.Client:
    """An httpx client whose every request is answered by `handler(request)`."""
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def json_response(data, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json"})


def arxiv_feed(entries: list[dict]) -> str:
    """An arXiv Atom feed with the given entries (id, title, authors, published, summary, doi)."""
    parts = []
    for e in entries:
        authors = "".join(f"<author><name>{a}</name></author>" for a in e.get("authors", []))
        doi = f"<arxiv:doi>{e['doi']}</arxiv:doi>" if e.get("doi") else ""
        parts.append(
            f"""<entry>
  <id>http://arxiv.org/abs/{e['id']}</id>
  <published>{e.get('published', '2021-03-04T00:00:00Z')}</published>
  <title>{e['title']}</title>
  <summary>{e.get('summary', 'An abstract.')}</summary>
  {authors}
  {doi}
  <link href="http://arxiv.org/abs/{e['id']}" rel="alternate" type="text/html"/>
  <link title="pdf" href="http://arxiv.org/pdf/{e['id']}" rel="related" type="application/pdf"/>
</entry>"""
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        + "".join(parts)
        + "</feed>"
    )


# --------------------------------------------------------------------------- DB


def add_paper(session, **overrides) -> Paper:
    fields = dict(source="s2", title="A Saved Paper", authors=["Ann Author"], year=2020, status="no_pdf")
    fields.update(overrides)
    paper = Paper(**fields)
    session.add(paper)
    session.commit()
    return paper


def add_ready_paper(session, sections: list[tuple[str, str]] | None = None, **overrides) -> Paper:
    """A paper with processed chunks (one per (section, text) pair), ready for AI features."""
    sections = sections or [
        ("Abstract", "We introduce widgets for software testing and report strong gains."),
        ("Introduction", "Testing large systems is difficult; the problem motivates our widget approach."),
        ("Method", "Our method builds widget graphs with a novel propagation algorithm."),
        ("Results", "Widgets improve accuracy by ten percent on the benchmark dataset."),
        ("Conclusion", "Widgets help; a limitation is that only small programs were studied."),
    ]
    paper = add_paper(session, **{"status": "ready", **overrides})
    paper.chunks = [
        Chunk(ordinal=i, section=s, text=t, page_start=i + 1, page_end=i + 1, n_tokens=estimate_tokens(t))
        for i, (s, t) in enumerate(sections)
    ]
    session.commit()
    return paper
