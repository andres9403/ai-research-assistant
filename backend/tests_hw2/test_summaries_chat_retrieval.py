"""Unit tests for context selection (summaries.py), citations and history (chat.py) and retrieval.py."""

import numpy as np
import pytest

from app.models import ChatMessage, Chunk, Paper
from app.services import chat, embeddings, retrieval, summaries
from app.services.retrieval import Retrieved

from tests_hw2.helpers import add_ready_paper


def chunk(ordinal, section, n_tokens=100, text=None, cid=None):
    return Chunk(id=cid or ordinal + 1, ordinal=ordinal, section=section, text=text or f"{section} text {ordinal}",
                 page_start=ordinal + 1, page_end=ordinal + 1, n_tokens=n_tokens)


# ----------------------------------------------------------------------- summaries


@pytest.mark.parametrize(
    "name, rank_name",
    [
        ("Abstract", r"abstract"),
        ("Conclusions", r"conclu|summary"),
        ("1 Introduction", r"introduction|overview"),
        ("Experimental Results", r"result|finding|evaluation|experiment|ablation|analysis|discussion"),
        ("Proposed Method", r"method|approach|model|architecture|framework|design|proposed"),
        ("Related Work", r"related work|prior work|literature"),
        ("Appendix A", r"appendi|supplement"),
        ("Acknowledging Cats", None),
    ],
)
def test_section_rank(name, rank_name):
    assert summaries.section_rank(name) == summaries.SECTION_PRIORITY.index(rank_name)


def test_select_context_prefers_important_sections_and_keeps_paper_order():
    chunks = [
        chunk(0, "Introduction", 3000),
        chunk(1, "Related Work", 3000),
        chunk(2, "Results", 3000),
        chunk(3, "Conclusion", 3000),
        chunk(4, "Abstract", 3000),
    ]
    context = summaries.select_context(chunks, budget=9000)
    assert [c.section for c in context.chunks] == ["Introduction", "Conclusion", "Abstract"]
    assert context.tokens == 9000


def test_select_context_gives_every_section_one_chunk_before_seconds():
    chunks = [chunk(0, "Abstract", 100), chunk(1, "Abstract", 100), chunk(2, "Method", 100), chunk(3, "Method", 100),
              chunk(4, "Related Work", 100)]
    context = summaries.select_context(chunks, budget=300)
    assert [c.ordinal for c in context.chunks] == [0, 2, 4]
    full = summaries.select_context(chunks, budget=10_000)
    assert [c.ordinal for c in full.chunks] == [0, 1, 2, 3, 4]


def test_select_context_skips_chunks_that_do_not_fit_but_takes_smaller_ones():
    chunks = [chunk(0, "Abstract", 900), chunk(1, "Results", 500), chunk(2, "Method", 50)]
    context = summaries.select_context(chunks, budget=1000)
    assert [c.ordinal for c in context.chunks] == [0, 2]
    assert context.tokens == 950


def test_select_context_with_nothing_that_fits():
    assert summaries.select_context([chunk(0, "Abstract", 9000)], budget=8000).chunks == []
    assert summaries.select_context([]).chunks == []


def test_context_sections_merge_consecutive_chunks():
    chunks = [chunk(0, "Results"), chunk(1, "Results"), chunk(2, "Conclusion")]
    assert summaries.Context(chunks, 300).sections == [
        {"section": "Results", "page_start": 1, "page_end": 2},
        {"section": "Conclusion", "page_start": 3, "page_end": 3},
    ]


def test_summary_prompt_formats_pages_and_title():
    paper = Paper(title="Widgets")
    c = chunk(0, "Results")
    c.page_end = 3
    prompt = summaries.build_prompt(paper, summaries.Context([c], 1))
    assert prompt.startswith("Paper title: Widgets")
    assert "[Results, pp. 1-3]" in prompt
    assert "<paper>" in prompt and "</paper>" in prompt


# ----------------------------------------------------------------------- citations


def excerpts(n):
    return [Retrieved(chunk(i, f"S{i + 1}", cid=100 + i), 0.5) for i in range(n)]


@pytest.mark.parametrize(
    "group, numbers",
    [("2", [2]), ("1, 3", [1, 3]), ("2-4", [2, 3, 4]), ("2–4; 6", [2, 3, 4, 6]), ("4-2", [4]), ("1-50", [1])],
)
def test_citation_number_groups(group, numbers):
    assert chat._numbers(group) == numbers


def test_cite_renumbers_in_order_of_first_use():
    text, citations = chat.cite("B is true [3]. A too [1, 3]. More [2-3].", excerpts(3))
    assert text == "B is true [1]. A too [2][1]. More [3][1]."
    assert [(c["n"], c["chunk_id"]) for c in citations] == [(1, 102), (2, 100), (3, 101)]
    assert citations[0]["section"] == "S3"
    assert citations[0]["text"] == "S3 text 2"


def test_cite_drops_markers_that_point_at_no_excerpt():
    text, citations = chat.cite("Out of range [7]. Zero [0]. Good [2].", excerpts(2))
    assert text == "Out of range. Zero. Good [1]."
    assert [c["chunk_id"] for c in citations] == [101]


def test_cite_without_markers():
    assert chat.cite("No markers at all.", excerpts(2)) == ("No markers at all.", [])


def test_strip_citations():
    assert chat.strip_citations("A [1] and B [2, 3].") == "A and B."


# ----------------------------------------------------------------------- history


def msg(role, content, background=None):
    return ChatMessage(role=role, content=content, background=background)


def test_history_keeps_recent_turns_within_budget_starting_on_a_question():
    paper = Paper(title="t")
    paper.messages = [msg("user", f"q{i}") if i % 2 == 0 else msg("assistant", f"a{i}") for i in range(10)]
    turns = chat.history(paper)
    assert [m.content for m in turns] == ["q4", "a5", "q6", "a7", "q8", "a9"]


def test_history_drops_turns_beyond_token_budget():
    paper = Paper(title="t")
    long = "x" * (chat.HISTORY_BUDGET_TOKENS * 3)  # one message fills the budget
    paper.messages = [msg("user", "old q"), msg("assistant", long), msg("user", "new q"), msg("assistant", "short")]
    assert [m.content for m in chat.history(paper)] == ["new q", "short"]


def test_history_never_starts_with_an_answer():
    paper = Paper(title="t")
    paper.messages = [msg("user", "q"), msg("assistant", "x" * 4500, background="y" * 10), msg("assistant", "a")]
    assert chat.history(paper) == []


def test_search_queries_join_follow_ups_to_previous_question():
    assert chat.search_queries("What is it?", []) == ["What is it?"]
    turns = [msg("user", "What is the method?"), msg("assistant", "It is X [1].")]
    assert chat.search_queries("And its limits?", turns) == ["And its limits?", "What is the method?\nAnd its limits?"]


def test_chat_prompt_numbers_excerpts_and_includes_background_of_earlier_answers():
    paper = Paper(title="Widgets")
    turns = [msg("user", "Q1"), msg("assistant", "A1 [2]", background="BG")]
    prompt = chat.build_prompt(paper, "Q2", excerpts(2), turns)
    assert "[1] S1, p. 1\nS1 text 0" in prompt
    assert "[2] S2, p. 2" in prompt
    assert "Assistant: A1\nBackground: BG" in prompt
    assert prompt.rstrip().endswith("Question: Q2")


# ----------------------------------------------------------------------- retrieval


def test_anchors_are_abstract_and_conclusion_or_first_chunk():
    chunks = [chunk(0, "Introduction"), chunk(1, "Abstract"), chunk(2, "Method"), chunk(3, "Conclusions")]
    assert [c.section for c in retrieval.anchors(chunks)] == ["Abstract", "Conclusions"]
    no_abstract = [chunk(0, "Introduction"), chunk(1, "Method")]
    assert [c.section for c in retrieval.anchors(no_abstract)] == ["Introduction"]


def test_section_bonus_nudges_matching_sections_once():
    chunks = [chunk(0, "Results"), chunk(1, "Related Work"), chunk(2, "Experiments")]
    bonus = retrieval.section_bonus("How does it perform on the benchmark results?", chunks)
    assert bonus.tolist() == pytest.approx([retrieval.HINT_BONUS, 0.0, retrieval.HINT_BONUS])
    assert retrieval.section_bonus("Tell me a joke", chunks).tolist() == [0.0, 0.0, 0.0]


def test_retrieve_returns_anchors_plus_best_matches_in_paper_order(session, fake_embedder):
    sections = [("Abstract", "overview of everything")] + [
        (f"Part {i}", f"filler topic number {i} unrelated words") for i in range(10)
    ] + [("Part X", "zebra stripes zebra habitat zebra"), ("Conclusion", "final remarks")]
    paper = add_ready_paper(session, sections=sections)
    found = retrieval.retrieve(session, paper, ["zebra stripes"], k=1)
    assert [r.chunk.section for r in found] == ["Abstract", "Part X", "Conclusion"]
    assert found[1].score > found[0].score
    assert all(c.embedding is not None for c in paper.chunks)  # embedded on first use


def test_retrieve_respects_the_token_budget(session):
    big = "word " * 600  # ~1000 tokens each
    paper = add_ready_paper(session, sections=[("Abstract", big), ("A", big), ("B", big), ("C", big)])
    found = retrieval.retrieve(session, paper, ["word"], budget=2500)
    assert sum(r.chunk.n_tokens for r in found) <= 2500
    assert len(found) == 2


def test_retrieve_with_no_chunks_or_queries(session):
    paper = add_ready_paper(session)
    assert retrieval.retrieve(session, paper, []) == []
    empty = Paper(title="e")
    assert retrieval.retrieve(session, empty, ["q"]) == []


def test_ensure_embeddings_only_embeds_missing_vectors(session, fake_embedder):
    paper = add_ready_paper(session)
    retrieval.ensure_embeddings(session, paper)
    assert sum(len(batch) for batch in fake_embedder.calls) == 5
    retrieval.ensure_embeddings(session, paper)
    assert sum(len(batch) for batch in fake_embedder.calls) == 5  # nothing left to embed
    paper.chunks[0].embedding = b"\x00" * 8  # a stale vector of the wrong dimension is re-embedded
    session.commit()
    retrieval.ensure_embeddings(session, paper)
    assert len(fake_embedder.calls[-1]) == 1


def test_embedding_bytes_round_trip():
    vector = np.array([0.25, -1.5, 3.0], dtype=np.float32)
    assert np.array_equal(embeddings.from_bytes(embeddings.to_bytes(vector)), vector)
    c = chunk(0, "Results")
    assert embeddings.passage_text(c) == "Results\nResults text 0"
    assert embeddings.has_embedding(c, 3) is False
    c.embedding = embeddings.to_bytes(vector)
    assert embeddings.has_embedding(c, 3) is True
    assert embeddings.has_embedding(c, 4) is False


def test_query_embedding_uses_bge_instruction_prefix(fake_embedder):
    embeddings.embed_query("what is it")
    assert fake_embedder.calls[-1] == [embeddings.QUERY_PREFIX + "what is it"]
