"""HTTP tests for summaries and Q&A (routers/ai.py), with the LLM and the embedding model faked."""

import pytest

from app.models import ChatMessage, Summary
from app.services import llm, summaries
from app.services.llm import LLMError

from tests_hw2.helpers import SUMMARY_DATA, add_paper, add_ready_paper

# ----------------------------------------------------------------------- summaries


def test_summary_is_generated_from_selected_sections_and_cached(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [SUMMARY_DATA]
    response = client.post(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["paper_id"] == paper.id
    assert body["content"] == SUMMARY_DATA
    assert body["provider"] == "anthropic"
    assert body["model"] == "fake-model"
    assert (body["input_tokens"], body["output_tokens"]) == (123, 45)
    assert [s["section"] for s in body["sections"]] == ["Abstract", "Introduction", "Method", "Results", "Conclusion"]

    call = fake_llm.calls[0]
    assert call["schema_name"] == "paper_summary"
    assert "Paper title: A Saved Paper" in call["prompt"]
    assert "[Results, p. 4]" in call["prompt"]

    # cached: a second POST and the GET return it without calling the LLM again
    assert client.post(f"/api/papers/{paper.id}/summary").json() == body
    assert client.get(f"/api/papers/{paper.id}/summary").json() == body
    assert len(fake_llm.calls) == 1


def test_summary_refresh_regenerates_and_replaces(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [SUMMARY_DATA, {**SUMMARY_DATA, "tldr": "Second take."}]
    client.post(f"/api/papers/{paper.id}/summary")
    body = client.post(f"/api/papers/{paper.id}/summary", params={"refresh": True}).json()
    assert body["content"]["tldr"] == "Second take."
    assert len(fake_llm.calls) == 2
    assert session.query(Summary).filter_by(paper_id=paper.id).count() == 1


def test_summary_fields_missing_from_llm_get_placeholder(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [{"tldr": "  Short   one. ", "problem": "", "approach": None}]
    content = client.post(f"/api/papers/{paper.id}/summary").json()["content"]
    assert content["tldr"] == "Short one."
    for field in ("problem", "approach", "data", "results", "limitations"):
        assert content[field] == summaries.NOT_COVERED


def test_get_summary_before_generation_is_404(client, session):
    paper = add_ready_paper(session)
    response = client.get(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 404
    assert response.json()["detail"] == "This paper has no summary yet"


def test_summary_without_any_llm_is_503(client, session):
    paper = add_ready_paper(session)
    response = client.post(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 503
    assert "No LLM is set up" in response.json()["detail"]


def test_cached_summary_is_served_even_without_an_llm(client, session):
    paper = add_ready_paper(session)
    paper.summary = Summary(provider="openai", model="m", content=SUMMARY_DATA, sections=[], input_tokens=1,
                            output_tokens=2)
    session.commit()
    response = client.post(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 200
    assert response.json()["provider"] == "openai"


@pytest.mark.parametrize("provider, message", [("openai", "OpenAI has no API key"), ("bogus", "Unknown LLM provider")])
def test_summary_with_unavailable_provider_is_503(client, session, fake_llm, provider, message):
    paper = add_ready_paper(session)
    response = client.post(f"/api/papers/{paper.id}/summary", params={"provider": provider})
    assert response.status_code == 503
    assert message in response.json()["detail"]
    assert fake_llm.calls == []


def test_summary_of_paper_without_text_is_409(client, session, fake_llm):
    paper = add_paper(session)
    response = client.post(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 409
    assert "Upload the PDF" in response.json()["detail"]
    assert fake_llm.calls == []


def test_summary_llm_failure_is_502_and_nothing_is_cached(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [LLMError("The model returned malformed JSON.")]
    response = client.post(f"/api/papers/{paper.id}/summary")
    assert response.status_code == 502
    assert response.json()["detail"] == "The model returned malformed JSON."
    assert client.get(f"/api/papers/{paper.id}/summary").status_code == 404


def test_summary_for_missing_paper_is_404(client, fake_llm):
    assert client.post("/api/papers/777/summary").status_code == 404


# ----------------------------------------------------------------------- chat


def answer(text, found=True, background=""):
    return {"answer": text, "found": found, "background": background}


def test_question_is_answered_with_renumbered_citations(client, session, fake_llm):
    paper = add_ready_paper(session)
    excerpts = {}

    def respond(call):
        # cite the Results excerpt, whatever number it has in the prompt, then the first one
        for line in call["prompt"].splitlines():
            if line.startswith("[") and "] " in line:
                n, rest = line[1:].split("] ", 1)
                excerpts[rest.split(",")[0]] = int(n)
        return answer(f"Accuracy rose ten percent [{excerpts['Results']}], per the abstract [1].",
                      background="Accuracy is the share of correct predictions [3].")

    fake_llm.responses = [respond]
    response = client.post(f"/api/papers/{paper.id}/chat", json={"question": "  What are the results?  "})
    assert response.status_code == 200
    body = response.json()
    assert body["question"]["role"] == "user"
    assert body["question"]["content"] == "What are the results?"
    reply = body["answer"]
    assert reply["role"] == "assistant"
    assert reply["content"] == "Accuracy rose ten percent [1], per the abstract [2]."
    assert [c["section"] for c in reply["citations"]] == ["Results", "Abstract"]
    assert [c["n"] for c in reply["citations"]] == [1, 2]
    assert "ten percent" in reply["citations"][0]["text"]
    assert reply["background"] == "Accuracy is the share of correct predictions."  # citations stripped
    assert reply["provider"] == "anthropic"
    assert fake_llm.calls[0]["schema_name"] == "paper_answer"
    assert "Question: What are the results?" in fake_llm.calls[0]["prompt"]

    thread = client.get(f"/api/papers/{paper.id}/chat").json()
    assert [m["role"] for m in thread] == ["user", "assistant"]
    assert thread[1]["citations"] == reply["citations"]


def test_follow_up_questions_include_the_conversation(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [answer("First [1]."), answer("Second [1].")]
    client.post(f"/api/papers/{paper.id}/chat", json={"question": "What is the method?"})
    client.post(f"/api/papers/{paper.id}/chat", json={"question": "And its limitations?"})
    prompt = fake_llm.calls[1]["prompt"]
    assert "<conversation>" in prompt
    assert "User: What is the method?" in prompt
    assert "Assistant: First." in prompt  # earlier markers are stripped
    assert len(client.get(f"/api/papers/{paper.id}/chat").json()) == 4


def test_not_found_answer_without_citations_is_accepted(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [answer("The excerpts don't say.", found=False)]
    response = client.post(f"/api/papers/{paper.id}/chat", json={"question": "Who funded it?"})
    assert response.status_code == 200
    assert response.json()["answer"]["citations"] == []


@pytest.mark.parametrize(
    "data, detail",
    [
        (answer("Claim without citations."), "cited none of the paper's passages"),
        (answer("Bogus citation [99]."), "cited none of the paper's passages"),
        (answer("   "), "empty answer"),
    ],
)
def test_unusable_answers_are_502_and_not_saved(client, session, fake_llm, data, detail):
    paper = add_ready_paper(session)
    fake_llm.responses = [data]
    response = client.post(f"/api/papers/{paper.id}/chat", json={"question": "What is new?"})
    assert response.status_code == 502
    assert detail in response.json()["detail"]
    assert client.get(f"/api/papers/{paper.id}/chat").json() == []


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "    "}, {"question": "x" * 2001}])
def test_invalid_questions_are_422(client, session, fake_llm, payload):
    paper = add_ready_paper(session)
    assert client.post(f"/api/papers/{paper.id}/chat", json=payload).status_code == 422
    assert fake_llm.calls == []


def test_question_on_paper_without_text_is_409(client, session, fake_llm):
    paper = add_paper(session, status="failed")
    response = client.post(f"/api/papers/{paper.id}/chat", json={"question": "Hi?"})
    assert response.status_code == 409


def test_question_without_llm_is_503(client, session):
    paper = add_ready_paper(session)
    assert client.post(f"/api/papers/{paper.id}/chat", json={"question": "Hi?"}).status_code == 503


def test_embedding_failure_is_503(client, session, fake_llm, fake_embedder):
    paper = add_ready_paper(session)
    fake_embedder.fail = True
    response = client.post(f"/api/papers/{paper.id}/chat", json={"question": "Hi?"})
    assert response.status_code == 503
    assert "embedding model" in response.json()["detail"]
    assert fake_llm.calls == []


def test_clear_chat_deletes_the_thread(client, session, fake_llm):
    paper = add_ready_paper(session)
    fake_llm.responses = [answer("Yes [1].")]
    client.post(f"/api/papers/{paper.id}/chat", json={"question": "Q?"})
    assert client.delete(f"/api/papers/{paper.id}/chat").status_code == 204
    assert client.get(f"/api/papers/{paper.id}/chat").json() == []
    assert session.query(ChatMessage).count() == 0
    assert client.delete(f"/api/papers/{paper.id}/chat").status_code == 204  # idempotent


def test_chat_endpoints_404_for_missing_paper(client):
    assert client.get("/api/papers/404/chat").status_code == 404
    assert client.delete("/api/papers/404/chat").status_code == 404
    assert client.post("/api/papers/404/chat", json={"question": "Q?"}).status_code == 404
