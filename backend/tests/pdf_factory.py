"""Builds small but realistic research-paper PDFs for the pipeline tests."""

import pymupdf

PAGE_W, PAGE_H = 595, 842
LEFT, BOTTOM = 72, 770

TITLE = "Sparse Expert Routing for Efficient Language Models"
AUTHORS = ["Ana María López", "Wei Zhang", "John O'Neil"]
ABSTRACT = (
    "We study sparse expert routing in large language models. Our router sends each "
    "token to two experts and cuts training cost by forty percent without hurting accuracy."
)


def sentences(topic: str, n: int) -> list[str]:
    return [f"Sentence {i} explains how {topic} behaves in our experiments." for i in range(n)]


class Writer:
    def __init__(self):
        self.doc = pymupdf.open()
        self.page = None
        self.y = BOTTOM
        self.new_page()

    def new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        number = self.doc.page_count
        # Running head and page number, which cleaning must remove.
        self.page.insert_text((LEFT, 40), "Preprint. Under review.", fontsize=8)
        self.page.insert_text((PAGE_W / 2, 815), str(number), fontsize=9)
        self.y = 90

    def line(self, text: str, size: float = 10, bold: bool = False, gap: float = 3, x: float = LEFT):
        if self.y > BOTTOM:
            self.new_page()
        self.page.insert_text((x, self.y), text, fontsize=size, fontname="hebo" if bold else "helv")
        self.y += size + gap

    def space(self, amount: float = 10):
        self.y += amount

    def paragraph(self, words: list[str], width: int = 11):
        for i in range(0, len(words), width):
            self.line(" ".join(words[i : i + width]))
        self.space(8)

    def bytes(self) -> bytes:
        return self.doc.tobytes()


def make_paper_pdf(
    title: str = TITLE,
    authors: list[str] = AUTHORS,
    arxiv_stamp: str | None = "arXiv:2401.01234v2 [cs.LG] 3 Jan 2024",
    body_paragraphs: int = 3,
) -> bytes:
    w = Writer()
    if arxiv_stamp:
        w.page.insert_text((30, 600), arxiv_stamp, fontsize=14, rotate=90)
    w.line(title, size=17, bold=True, gap=10)
    w.line(", ".join(authors[:-1]) + f" and {authors[-1]}" if len(authors) > 1 else authors[0], size=11)
    w.line("Department of Computer Science, Example University", size=9)
    w.line("{alopez, wzhang}@example.edu", size=9)
    w.space(14)

    w.line("Abstract", size=12, bold=True, gap=6)
    w.paragraph(ABSTRACT.split())

    w.line("1 Introduction", size=12, bold=True, gap=6)
    # A word hyphenated across a line break, which cleaning must rejoin.
    w.line("Mixture-of-experts models route each token to a few experts of a large trans-")
    w.line("former network, which keeps compute low while the parameter count grows.")
    w.space(8)
    for p in range(body_paragraphs):
        w.paragraph(" ".join(sentences(f"routing idea {p}", 12)).split())

    w.line("2 Method", size=12, bold=True, gap=6)
    for p in range(body_paragraphs):
        w.paragraph(" ".join(sentences(f"the router {p}", 14)).split())

    w.line("3 Results", size=12, bold=True, gap=6)
    w.line("Model")  # a table header cell at body size: not a heading
    w.line("Ours 41.2")
    w.space(8)
    for p in range(body_paragraphs):
        w.paragraph(" ".join(sentences(f"accuracy {p}", 12)).split())

    w.line("References", size=12, bold=True, gap=6)
    for i in range(1, 15):
        w.line(f"[{i}] A. Author and B. Writer. Reference work number {i}. In Venue, 2020.")

    w.line("A Additional Proofs", size=12, bold=True, gap=6)
    w.paragraph(" ".join(sentences("the appendix lemma", 6)).split())
    return w.bytes()


def make_scanned_pdf() -> bytes:
    """A PDF with only drawings and no text layer, like a scan."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(72, 72, 300, 300), fill=(0.2, 0.2, 0.2))
    return doc.tobytes()


def make_ieee_pdf() -> bytes:
    """Roman-numeral headings and an inline "Abstract." paragraph (LNCS/IEEE style).

    Base-14 fonts cannot draw an em dash, so "Abstract—" is unit-tested separately.
    """
    w = Writer()
    w.line("Graph Neural Networks for Molecule Property Prediction", size=20, gap=10)
    w.line("Maria Rossi, Kenji Tanaka", size=11)
    w.space(14)
    w.line("Abstract. We predict molecular properties with message passing networks", bold=True)
    w.line("and report results on three public benchmarks.", bold=True)
    w.space(8)
    w.line("I. INTRODUCTION", size=10, gap=6)
    w.paragraph(" ".join(sentences("message passing", 10)).split())
    w.line("II. RELATED WORK", size=10, gap=6)
    w.paragraph(" ".join(sentences("prior graph work", 10)).split())
    return w.bytes()


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
