from app.db import SessionLocal
from app.models import Chunk, Summary
from app.services import llm, summaries
from tests.pdf_factory import make_paper_pdf
from tests.test_papers import paper as paper_payload
from tests.test_pdf_api import upload

SUMMARY = {
    "tldr": "A router sends each token to two experts and cuts training cost.",
    "problem": "Mixture-of-experts models are costly to train.",
    "approach": "A learned router picks two experts per token.",
    "data": "",  # left blank by the model
    "results": "Training cost falls by forty percent at equal accuracy.",
    "limitations": "The authors state none.",
}


def summary_calls(fake):
    return [c for c in fake.calls if c["schema_name"] == "paper_summary"]


def chunk(ordinal, section, tokens):
    return Chunk(ordinal=ordinal, section=section, text=f"{section} {ordinal}", page_start=1, page_end=1, n_tokens=tokens)


def test_context_gives_every_section_its_opening_chunk_first():
    chunks = [
        chunk(0, "Abstract", 100),
        chunk(1, "Introduction", 300),
        chunk(2, "Introduction", 300),
        chunk(3, "Introduction", 300),
        chunk(4, "Related Work", 300),
        chunk(5, "Method", 300),
        chunk(6, "Method", 300),
        chunk(7, "Results", 300),
        chunk(8, "Conclusion", 200),
        chunk(9, "Appendix", 300),
    ]
    context = summaries.select_context(chunks, budget=1300)
    # Round one, best sections first: Abstract, Conclusion, Introduction, Results,
    # Method (1200 so far); Related Work and the appendix no longer fit.
    assert [c.ordinal for c in context.chunks] == [0, 1, 5, 7, 8]
    assert context.tokens == 1200

    context = summaries.select_context(chunks, budget=2500)
    # Round one now fits every section (1800); round two adds the second chunks of
    # Introduction and Method (2400), and the third Introduction chunk doesn't fit.
    assert [c.ordinal for c in context.chunks] == [0, 1, 2, 4, 5, 6, 7, 8, 9]
    assert context.tokens == 2400


def test_context_sends_everything_when_it_fits():
    chunks = [chunk(i, "Full text", 500) for i in range(5)]
    context = summaries.select_context(chunks)
    assert [c.ordinal for c in context.chunks] == [0, 1, 2, 3, 4]
    assert context.sections == [{"section": "Full text", "page_start": 1, "page_end": 1}]


def test_long_paper_is_capped_at_the_budget():
    chunks = [chunk(i, f"Section {i // 10}", 330) for i in range(100)]
    context = summaries.select_context(chunks)
    assert summaries.CONTEXT_BUDGET_TOKENS - 330 < context.tokens <= summaries.CONTEXT_BUDGET_TOKENS


def test_section_rank():
    rank = summaries.section_rank
    assert rank("Abstract") < rank("Conclusions") < rank("1 Introduction") < rank("Results") < rank("Method")
    assert rank("Method") < rank("Our Dilated Attention") < rank("Related Work") < rank("Appendix B")


def test_summary_is_generated_from_the_paper_text_and_cached(client, fake_llm):
    fake_llm.answers["paper_summary"] = SUMMARY
    paper_id = upload(client).json()["id"]
    url = f"/api/papers/{paper_id}/summary"
    assert client.get(url).status_code == 404

    resp = client.post(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"]["tldr"] == SUMMARY["tldr"]
    assert data["content"]["data"] == summaries.NOT_COVERED
    assert (data["provider"], data["model"]) == ("anthropic", "fake-model")
    assert (data["input_tokens"], data["output_tokens"]) == (1234, 321)
    assert [s["section"] for s in data["sections"]] == [
        "Abstract", "Introduction", "Method", "Results", "Additional Proofs"
    ]

    # The prompt carries the paper's own text, by section, and never the references.
    [call] = summary_calls(fake_llm)
    assert call["schema"] == summaries.SCHEMA
    assert "Our router sends each token to two experts" in call["prompt"]
    assert "[Method, p. " in call["prompt"]
    assert "Sentence 3 explains how accuracy 2 behaves" in call["prompt"]
    assert "Reference work number" not in call["prompt"]
    assert "<paper>" in call["prompt"] and "Sparse Expert Routing" in call["prompt"]

    # Cached: GET and a second POST answer without calling the LLM again.
    assert client.get(url).json() == data
    assert client.post(url).json() == data
    assert len(summary_calls(fake_llm)) == 1


def test_refresh_regenerates_and_replaces_the_summary(client, fake_llm):
    fake_llm.answers["paper_summary"] = SUMMARY
    paper_id = upload(client).json()["id"]
    url = f"/api/papers/{paper_id}/summary"
    client.post(url)

    fake_llm.answers["paper_summary"] = {**SUMMARY, "tldr": "A newer take."}
    resp = client.post(url, params={"refresh": True})
    assert resp.json()["content"]["tldr"] == "A newer take."
    assert len(summary_calls(fake_llm)) == 2
    with SessionLocal() as session:
        assert session.query(Summary).count() == 1


def test_provider_is_chosen_per_request(client, fake_llm, monkeypatch):
    fake_llm.answers["paper_summary"] = SUMMARY
    other = type(fake_llm)(name="openai", model="fake-gpt")
    other.answers["paper_summary"] = {**SUMMARY, "tldr": "From the other model."}
    monkeypatch.setattr(llm, "available", lambda: {"anthropic": fake_llm, "openai": other})
    paper_id = upload(client).json()["id"]

    data = client.post(f"/api/papers/{paper_id}/summary", params={"provider": "openai"}).json()
    assert (data["provider"], data["model"], data["content"]["tldr"]) == ("openai", "fake-gpt", "From the other model.")
    assert summary_calls(fake_llm) == []


def test_summary_needs_an_llm(client):
    paper_id = upload(client).json()["id"]
    resp = client.post(f"/api/papers/{paper_id}/summary")
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_unconfigured_provider_is_refused(client, fake_llm):
    fake_llm.answers["paper_summary"] = SUMMARY
    paper_id = upload(client).json()["id"]
    resp = client.post(f"/api/papers/{paper_id}/summary", params={"provider": "openai"})
    assert resp.status_code == 503
    assert "OpenAI has no API key" in resp.json()["detail"]


def test_summary_needs_the_full_text(client, fake_llm):
    paper_id = client.post("/api/papers", json=paper_payload()).json()["id"]
    resp = client.post(f"/api/papers/{paper_id}/summary")
    assert resp.status_code == 409
    assert "Upload the PDF" in resp.json()["detail"]
    assert summary_calls(fake_llm) == []


def test_llm_failure_is_reported_and_nothing_is_cached(client, fake_llm):
    fake_llm.answers["paper_summary"] = llm.LLMError("Anthropic is rate-limiting requests.")
    paper_id = upload(client).json()["id"]
    resp = client.post(f"/api/papers/{paper_id}/summary")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Anthropic is rate-limiting requests."
    assert client.get(f"/api/papers/{paper_id}/summary").status_code == 404


def test_replacing_the_pdf_drops_the_stale_summary(client, fake_llm):
    fake_llm.answers["paper_summary"] = SUMMARY
    paper_id = upload(client).json()["id"]
    client.post(f"/api/papers/{paper_id}/summary")

    replacement = make_paper_pdf(body_paragraphs=1)
    resp = client.post(f"/api/papers/{paper_id}/pdf", files={"file": ("v2.pdf", replacement, "application/pdf")})
    assert resp.status_code == 200
    assert client.get(f"/api/papers/{paper_id}/summary").status_code == 404


def test_deleting_a_paper_deletes_its_summary(client, fake_llm):
    fake_llm.answers["paper_summary"] = SUMMARY
    paper_id = upload(client).json()["id"]
    client.post(f"/api/papers/{paper_id}/summary")
    assert client.delete(f"/api/papers/{paper_id}").status_code == 204
    with SessionLocal() as session:
        assert session.query(Summary).count() == 0
