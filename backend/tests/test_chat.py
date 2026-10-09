import sqlite3

import numpy as np
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import ChatMessage, Chunk, Paper
from app.services import chat, embeddings, llm, retrieval
from tests.conftest import TEST_DB_PATH
from tests.pdf_factory import make_paper_pdf
from tests.test_papers import paper as paper_payload
from tests.test_pdf_api import upload

# The demo's example questions; the suggestion chips in the Chat tab ask the same.
EXAMPLE_QUESTIONS = [
    "What problem does this paper address?",
    "What method or approach do the authors propose?",
    "What data or experiments do they use to evaluate it?",
    "What are the main results?",
    "What limitations or future work do the authors mention?",
]


def answer_calls(fake):
    return [c for c in fake.calls if c["schema_name"] == "paper_answer"]


def excerpts_in(prompt):
    """The [n] Section headers of the excerpts block, in order."""
    block = prompt.split("<excerpts>\n")[1].split("\n</excerpts>")[0]
    return [line for line in block.split("\n") if line.startswith("[")]


def make_chunk(ordinal, section, text, tokens=100):
    return Chunk(id=ordinal + 100, ordinal=ordinal, section=section, text=text, page_start=ordinal + 1,
                 page_end=ordinal + 1, n_tokens=tokens)


def ask(client, paper_id, question, **params):
    return client.post(f"/api/papers/{paper_id}/chat", json={"question": question}, params=params)


# --- embeddings ---------------------------------------------------------------

def test_upload_embeds_every_chunk(client, fake_embedder):
    paper_id = upload(client).json()["id"]
    with SessionLocal() as session:
        chunks = session.get(Paper, paper_id).chunks
        assert chunks and all(embeddings.has_embedding(c, fake_embedder.dim) for c in chunks)
        vector = embeddings.from_bytes(chunks[0].embedding)
        assert vector.dtype == np.float32 and abs(np.linalg.norm(vector) - 1) < 1e-5
    # One batch, and each passage carries its section name.
    [batch] = fake_embedder.calls
    assert len(batch) == len(chunks)
    assert batch[0].startswith("Abstract\n")


def test_attaching_a_pdf_embeds_it_too(client, fake_embedder):
    paper_id = client.post("/api/papers", json=paper_payload(pdf_url=None)).json()["id"]
    resp = client.post(f"/api/papers/{paper_id}/pdf", files={"file": ("p.pdf", make_paper_pdf(), "application/pdf")})
    assert resp.status_code == 200
    with SessionLocal() as session:
        chunks = session.get(Paper, paper_id).chunks
        assert chunks and all(c.embedding is not None for c in chunks)


def test_a_paper_embedded_meanwhile_is_not_embedded_twice(client, fake_embedder):
    paper_id = upload(client).json()["id"]
    with SessionLocal() as session:
        for c in session.get(Paper, paper_id).chunks:
            c.embedding = None
        session.commit()

    with SessionLocal() as asking, SessionLocal() as background:
        stale = asking.get(Paper, paper_id)
        assert all(c.embedding is None for c in stale.chunks)  # loaded before the background run
        retrieval.ensure_embeddings(background, background.get(Paper, paper_id))
        calls = len(fake_embedder.calls)
        retrieval.ensure_embeddings(asking, stale)
        assert len(fake_embedder.calls) == calls
        assert all(c.embedding is not None for c in stale.chunks)


def test_embed_chunks_only_embeds_what_is_missing(fake_embedder):
    chunks = [make_chunk(0, "Intro", "router experts"), make_chunk(1, "Method", "gating network")]
    assert embeddings.embed_chunks(chunks) == 2
    assert embeddings.embed_chunks(chunks) == 0
    chunks[1].embedding = b"\x00" * 12  # a vector from a model of another size
    assert embeddings.embed_chunks(chunks) == 1
    assert fake_embedder.calls[-1] == ["Method\ngating network"]


def test_queries_get_the_bge_instruction(fake_embedder):
    embeddings.embed_query("What datasets are used?")
    assert fake_embedder.calls == [[embeddings.QUERY_PREFIX + "What datasets are used?"]]


def test_failed_embedding_does_not_fail_the_upload(client, fake_embedder):
    fake_embedder.error = embeddings.EmbeddingError("no model")
    resp = upload(client)
    assert resp.status_code == 201
    assert resp.json()["status"] == "ready"
    with SessionLocal() as session:
        assert all(c.embedding is None for c in session.get(Paper, resp.json()["id"]).chunks)


# --- retrieval ----------------------------------------------------------------

def paper_with(chunks):
    paper = Paper(source="upload", title="T", authors=[])
    paper.chunks = chunks
    embeddings.embed_chunks(chunks)
    return paper


class NoSession:
    def commit(self):
        pass


def test_retrieve_ranks_by_similarity_and_returns_paper_order():
    paper = paper_with([
        make_chunk(0, "Abstract", "language models grow large"),
        make_chunk(1, "Method", "router gating experts tokens"),
        make_chunk(2, "Results", "perplexity benchmark scores"),
        make_chunk(3, "Discussion", "router experts tradeoff"),
        make_chunk(4, "Conclusion", "we summarise the paper"),
    ])
    found = retrieval.retrieve(NoSession(), paper, ["router experts"], k=2)
    # The abstract and conclusion anchors, plus the two best matches; in paper order.
    assert [r.chunk.ordinal for r in found] == [0, 1, 3, 4]
    assert found[2].score > found[1].score > found[0].score


def test_anchors_are_the_abstract_and_conclusion_or_the_opening_chunk():
    chunks = [make_chunk(0, "Front", "a"), make_chunk(1, "Abstract", "b"), make_chunk(2, "Abstract", "c"),
              make_chunk(3, "5 Conclusions and Future Work", "d"), make_chunk(4, "Appendix", "e")]
    assert [c.ordinal for c in retrieval.anchors(chunks)] == [1, 3]
    assert [c.ordinal for c in retrieval.anchors(chunks[3:])] == [3]
    assert [c.ordinal for c in retrieval.anchors(chunks[4:])] == [4]


def test_questions_naming_a_kind_of_content_nudge_its_sections():
    chunks = [make_chunk(0, "Introduction", ""), make_chunk(1, "4 Experimental Results", ""),
              make_chunk(2, "Limitations", ""), make_chunk(3, "Conclusion", "")]
    bonus = retrieval.HINT_BONUS
    assert list(retrieval.section_bonus("What are the main results?", chunks)) == [0, bonus, 0, 0]
    assert list(retrieval.section_bonus("Any limitations or future work?", chunks)) == [0, 0, bonus, bonus]
    assert list(retrieval.section_bonus("What problem do they address?", chunks)) == [bonus, 0, 0, 0]
    assert list(retrieval.section_bonus("Who funded it?", chunks)) == [0, 0, 0, 0]
    # Matching several hints never stacks past one bonus.
    assert retrieval.section_bonus("results and experiments", chunks).max() == bonus

    paper = paper_with([make_chunk(0, "Abstract", "x"), make_chunk(1, "Method", "router accuracy"),
                        make_chunk(2, "Results", "router accuracy")])
    # Equal text: the section bonus decides.
    [_, best] = retrieval.retrieve(NoSession(), paper, ["What results does the router reach?"], k=1)
    assert best.chunk.section == "Results"


def test_retrieve_respects_the_token_budget():
    paper = paper_with([make_chunk(i, "Results", f"accuracy result {i}", tokens=1000) for i in range(5)])
    found = retrieval.retrieve(NoSession(), paper, ["accuracy"], k=6, budget=2500)
    assert len(found) == 2  # the opening anchor and one more


def test_retrieve_takes_the_best_score_across_queries():
    paper = paper_with([
        make_chunk(0, "Abstract", "an overview"),
        make_chunk(1, "Method", "router gating experts"),
        make_chunk(2, "Limitations", "memory overhead limitation"),
    ])
    [_, only] = retrieval.retrieve(NoSession(), paper, ["memory overhead"], k=1)
    assert only.chunk.ordinal == 2
    found = retrieval.retrieve(NoSession(), paper, ["memory overhead", "router gating"], k=2)
    assert [r.chunk.ordinal for r in found] == [0, 1, 2]


def test_chunks_from_before_m4_are_embedded_on_the_first_question(client, fake_llm, fake_embedder):
    fake_llm.answers["paper_answer"] = {"answer": "It routes tokens [1]."}
    paper_id = upload(client).json()["id"]
    with SessionLocal() as session:
        for c in session.get(Paper, paper_id).chunks:
            c.embedding = None
        session.commit()

    assert ask(client, paper_id, "How are tokens routed?").status_code == 200
    with SessionLocal() as session:
        assert all(c.embedding is not None for c in session.get(Paper, paper_id).chunks)
    calls = len(fake_embedder.calls)
    ask(client, paper_id, "And the cost?")
    # Only the two queries are embedded the second time, not the chunks again.
    assert len(fake_embedder.calls) == calls + 2


# --- citations ----------------------------------------------------------------

def excerpts(n):
    return [retrieval.Retrieved(make_chunk(i, f"Section {i}", f"text {i}"), 0.5) for i in range(n)]


def test_citations_are_renumbered_in_order_of_use():
    text, cited = chat.cite("First [3]. Second [1][3]. Third [2, 3].", excerpts(3))
    assert text == "First [1]. Second [2][1]. Third [3][1]."
    assert [(c["n"], c["section"]) for c in cited] == [(1, "Section 2"), (2, "Section 0"), (3, "Section 1")]
    assert cited[0] == {"n": 1, "chunk_id": 102, "section": "Section 2", "page_start": 3, "page_end": 3,
                        "text": "text 2"}


def test_markers_that_point_at_no_excerpt_are_dropped():
    text, cited = chat.cite("The paper cites prior work [12]. Routing helps [2]. Both [2; 9].", excerpts(2))
    assert text == "The paper cites prior work. Routing helps [1]. Both [1]."
    assert [c["section"] for c in cited] == ["Section 1"]


def test_citation_ranges_and_duplicates():
    text, cited = chat.cite("All of it [1-3]. Again [2, 2].", excerpts(3))
    assert text == "All of it [1][2][3]. Again [2]."
    assert len(cited) == 3


def test_answer_without_markers_has_no_citations():
    assert chat.cite("The excerpts don't say.", excerpts(2)) == ("The excerpts don't say.", [])


# --- the chat API -------------------------------------------------------------

def test_question_is_answered_from_retrieved_passages_with_citations(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "Accuracy holds steady [2], as the results show [1]."}
    paper_id = upload(client).json()["id"]

    resp = ask(client, paper_id, "  What accuracy do the results report?  ")
    assert resp.status_code == 200
    question, answer = resp.json()["question"], resp.json()["answer"]
    assert (question["role"], question["content"]) == ("user", "What accuracy do the results report?")
    assert answer["role"] == "assistant"
    assert answer["content"] == "Accuracy holds steady [1], as the results show [2]."
    assert (answer["provider"], answer["model"]) == ("anthropic", "fake-model")
    assert (answer["input_tokens"], answer["output_tokens"]) == (1234, 321)

    # The prompt has only the abstract anchor (the paper has no conclusion) and the
    # top excerpts, numbered in paper order; both Results chunks are among them, and
    # neither references nor the whole paper are sent.
    [call] = answer_calls(fake_llm)
    assert call["schema"] == chat.SCHEMA
    headers = excerpts_in(call["prompt"])
    assert len(headers) == retrieval.TOP_K + 1
    assert headers[0] == "[1] Abstract, p. 1"
    assert [h.split()[0] for h in headers] == [f"[{n}]" for n in range(1, len(headers) + 1)]
    assert sum(" Results, " in h for h in headers) == 2

    # Citation 1 is the excerpt the model called [2], citation 2 its [1].
    cited = [f"{c['section']}, p" for c in answer["citations"]]
    assert [c["n"] for c in answer["citations"]] == [1, 2]
    assert headers[1].startswith(f"[2] {cited[0]}") and headers[0].startswith(f"[1] {cited[1]}")
    assert answer["citations"][0]["text"] in call["prompt"]
    assert "Reference work number" not in call["prompt"]
    assert "<conversation>" not in call["prompt"]
    assert call["prompt"].endswith("Question: What accuracy do the results report?")
    with SessionLocal() as session:
        paper_tokens = sum(c.n_tokens for c in session.get(Paper, paper_id).chunks)
    assert len(call["prompt"]) / 4 < min(paper_tokens, retrieval.CONTEXT_BUDGET_TOKENS + 200)

    # The thread is saved.
    thread = client.get(f"/api/papers/{paper_id}/chat").json()
    assert [m["role"] for m in thread] == ["user", "assistant"]
    assert thread[1] == answer


def test_follow_ups_carry_the_conversation(client, fake_llm, fake_embedder):
    fake_llm.answers["paper_answer"] = {"answer": "The router picks two experts [1]."}
    paper_id = upload(client).json()["id"]
    ask(client, paper_id, "How does the router pick experts?")
    fake_embedder.calls.clear()

    fake_llm.answers["paper_answer"] = {"answer": "Cost falls by forty percent [1]."}
    ask(client, paper_id, "And what does it save?")
    prompt = answer_calls(fake_llm)[1]["prompt"]
    conversation = prompt.split("<conversation>\n")[1].split("\n</conversation>")[0]
    # Earlier markers are stripped: they numbered other excerpts.
    assert conversation == "User: How does the router pick experts?\n\nAssistant: The router picks two experts."
    # The follow-up is searched alone and joined to the question before it.
    queries = [call[0] for call in fake_embedder.calls]
    assert queries == [
        embeddings.QUERY_PREFIX + "And what does it save?",
        embeddings.QUERY_PREFIX + "How does the router pick experts?\nAnd what does it save?",
    ]
    assert len(client.get(f"/api/papers/{paper_id}/chat").json()) == 4


def test_history_is_capped():
    paper = Paper(source="upload", title="T", authors=[])
    paper.messages = [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(10)]
    assert [m.content for m in chat.history(paper)] == ["m4", "m5", "m6", "m7", "m8", "m9"]

    paper.messages[-1].content = "x" * 4 * (chat.HISTORY_BUDGET_TOKENS - 2)
    assert [m.content for m in chat.history(paper)] == ["m8", paper.messages[-1].content]
    paper.messages[-1].content = "x" * 4 * chat.HISTORY_BUDGET_TOKENS
    # Only the long last answer fits, and an answer alone would lack its question.
    assert chat.history(paper) == []


def test_the_five_example_questions_are_answered_with_citations(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "From the paper [1] and [2]."}
    paper_id = upload(client).json()["id"]
    for question in EXAMPLE_QUESTIONS:
        answer = ask(client, paper_id, question).json()["answer"]
        assert [c["n"] for c in answer["citations"]] == [1, 2]
    assert len(client.get(f"/api/papers/{paper_id}/chat").json()) == 10


def test_provider_is_chosen_per_question(client, fake_llm, monkeypatch):
    other = type(fake_llm)(name="openai", model="fake-gpt")
    other.answers["paper_answer"] = {"answer": "From the other model [1]."}
    monkeypatch.setattr(llm, "available", lambda: {"anthropic": fake_llm, "openai": other})
    paper_id = upload(client).json()["id"]
    answer = ask(client, paper_id, "What is routed?", provider="openai").json()["answer"]
    assert (answer["provider"], answer["model"]) == ("openai", "fake-gpt")
    assert answer_calls(fake_llm) == []


def test_chat_needs_an_llm(client):
    paper_id = upload(client).json()["id"]
    resp = ask(client, paper_id, "What is routed?")
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_chat_needs_the_full_text(client, fake_llm):
    paper_id = client.post("/api/papers", json=paper_payload()).json()["id"]
    resp = ask(client, paper_id, "What is routed?")
    assert resp.status_code == 409
    assert "Upload the PDF" in resp.json()["detail"]


def test_blank_or_huge_questions_are_rejected(client, fake_llm):
    paper_id = upload(client).json()["id"]
    assert ask(client, paper_id, "   ").status_code == 422
    assert ask(client, paper_id, "x" * 2001).status_code == 422
    assert answer_calls(fake_llm) == []


def test_llm_failure_saves_nothing(client, fake_llm):
    fake_llm.answers["paper_answer"] = llm.LLMError("Anthropic is rate-limiting requests.")
    paper_id = upload(client).json()["id"]
    resp = ask(client, paper_id, "What is routed?")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Anthropic is rate-limiting requests."
    assert client.get(f"/api/papers/{paper_id}/chat").json() == []


def test_empty_answer_is_an_error(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "   "}
    paper_id = upload(client).json()["id"]
    assert ask(client, paper_id, "What is routed?").status_code == 502
    assert client.get(f"/api/papers/{paper_id}/chat").json() == []


def test_embedding_model_failure_is_reported(client, fake_llm, fake_embedder):
    fake_llm.answers["paper_answer"] = {"answer": "x [1]"}
    paper_id = upload(client).json()["id"]
    fake_embedder.error = embeddings.EmbeddingError("The local embedding model couldn't be loaded.")
    resp = ask(client, paper_id, "What is routed?")
    assert resp.status_code == 503
    assert "embedding model" in resp.json()["detail"]
    assert answer_calls(fake_llm) == []


def test_clear_chat(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "Two experts [1]."}
    paper_id = upload(client).json()["id"]
    ask(client, paper_id, "How many experts?")
    assert client.delete(f"/api/papers/{paper_id}/chat").status_code == 204
    assert client.get(f"/api/papers/{paper_id}/chat").json() == []


def test_replacing_the_pdf_clears_the_chat(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "Two experts [1]."}
    paper_id = upload(client).json()["id"]
    ask(client, paper_id, "How many experts?")
    replacement = make_paper_pdf(body_paragraphs=1)
    resp = client.post(f"/api/papers/{paper_id}/pdf", files={"file": ("v2.pdf", replacement, "application/pdf")})
    assert resp.status_code == 200
    assert client.get(f"/api/papers/{paper_id}/chat").json() == []


def test_deleting_a_paper_deletes_its_chat(client, fake_llm):
    fake_llm.answers["paper_answer"] = {"answer": "Two experts [1]."}
    paper_id = upload(client).json()["id"]
    ask(client, paper_id, "How many experts?")
    assert client.delete(f"/api/papers/{paper_id}").status_code == 204
    with SessionLocal() as session:
        assert session.query(ChatMessage).count() == 0


def test_m3_database_gets_the_embedding_column_and_chat_table(client):
    client.__exit__(None, None, None)
    Base.metadata.drop_all(engine)
    with sqlite3.connect(TEST_DB_PATH) as conn:  # the chunks table exactly as M2/M3 created it
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, paper_id INTEGER, ordinal INTEGER, section TEXT,"
            " text TEXT, page_start INTEGER, page_end INTEGER, n_tokens INTEGER)"
        )

    with TestClient(app) as restarted:
        assert upload(restarted).status_code == 201
    with sqlite3.connect(TEST_DB_PATH) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
        assert "embedding" in columns
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone() == (0,)
