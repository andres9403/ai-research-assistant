import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useLlm } from "../llm.jsx";

// Offered while the thread is empty; the tutorial's demo asks the same five.
const EXAMPLES = [
  "What problem does this paper address?",
  "What method or approach do the authors propose?",
  "What data or experiments do they use to evaluate it?",
  "What are the main results?",
  "What limitations or future work do the authors mention?",
];

function pages(c) {
  return c.page_start === c.page_end ? `p. ${c.page_start}` : `pp. ${c.page_start}–${c.page_end}`;
}

// `version` changes whenever the paper is reloaded (e.g. after a new PDF), which
// clears the thread on the server.
export default function ChatPanel({ paper, version }) {
  const llm = useLlm();
  const [thread, setThread] = useState({ status: "loading" });
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(null); // the question being answered
  const [error, setError] = useState(null);
  const endRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getChat(paper.id)
      .then((messages) => !cancelled && setThread({ status: "ok", messages }))
      .catch((err) => !cancelled && setThread({ status: "error", error: err.message }));
    return () => {
      cancelled = true;
    };
  }, [paper.id, version]);

  const count = thread.messages?.length ?? 0;
  useEffect(() => {
    if (count || pending) endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [count, pending]);

  async function ask(text) {
    const q = text.trim();
    if (!q || pending) return;
    setPending(q);
    setError(null);
    setQuestion("");
    try {
      const { question: asked, answer } = await api.ask(paper.id, q, llm.provider);
      setThread((t) => ({ status: "ok", messages: [...(t.messages || []), asked, answer] }));
    } catch (err) {
      setError(err.message);
      setQuestion(q); // give the question back so it can be retried
    } finally {
      setPending(null);
    }
  }

  async function clear() {
    if (!window.confirm("Clear this paper's conversation?")) return;
    try {
      await api.clearChat(paper.id);
      setThread({ status: "ok", messages: [] });
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  const ready = paper.status === "ready";
  const model = llm.current;
  const canAsk = ready && model;

  if (thread.status === "loading") return <p className="muted">Loading…</p>;
  if (thread.status === "error") return <p className="error">Could not load the conversation: {thread.error}</p>;

  const messages = thread.messages;

  return (
    <div className="chat">
      {!ready && (
        <p className="muted">
          Answers are drawn from the paper's full text, which isn't available yet. Upload the PDF to enable AI
          features.
        </p>
      )}
      {ready && !model && (
        <p className="muted">
          No AI model is set up. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend.
        </p>
      )}

      {messages.length === 0 && !pending && canAsk && (
        <div className="chat-empty">
          <p className="muted">
            Ask anything about this paper. Each question sends only the abstract, the conclusion and the passages
            most relevant to it (about 3k tokens) to the model, and every answer cites the passages it used.
          </p>
          <div className="examples">
            {EXAMPLES.map((example) => (
              <button key={example} className="secondary example" onClick={() => ask(example)}>
                {example}
              </button>
            ))}
          </div>
        </div>
      )}

      {(messages.length > 0 || pending) && (
        <div className="messages">
          {messages.map((m) =>
            m.role === "user" ? <Question key={m.id} text={m.content} /> : <Answer key={m.id} message={m} />
          )}
          {pending && (
            <>
              <Question text={pending} />
              <div className="answer muted">Reading the relevant passages with {model?.model}…</div>
            </>
          )}
          <div ref={endRef} />
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {canAsk && (
        <form
          className="ask"
          onSubmit={(e) => {
            e.preventDefault();
            ask(question);
          }}
        >
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                ask(question);
              }
            }}
            placeholder="Ask a question about this paper…"
            rows={2}
            maxLength={2000}
            disabled={Boolean(pending)}
          />
          <div className="row">
            <button type="submit" disabled={Boolean(pending) || !question.trim()}>
              {pending ? "Answering…" : "Ask"}
            </button>
            {messages.length > 0 && (
              <button type="button" className="secondary" onClick={clear} disabled={Boolean(pending)}>
                Clear conversation
              </button>
            )}
            <span className="muted small">Enter to send · Shift+Enter for a new line</span>
          </div>
        </form>
      )}
    </div>
  );
}

function Question({ text }) {
  return <div className="question">{text}</div>;
}

function Answer({ message }) {
  const [open, setOpen] = useState(null); // the citation number shown in full
  const parts = message.content.split(/(\[\d+\])/);
  const known = new Set(message.citations.map((c) => c.n));

  function toggle(n) {
    setOpen((current) => (current === n ? null : n));
  }

  return (
    <div className="answer">
      <p className="answer-text">
        {parts.map((part, i) => {
          const marker = part.match(/^\[(\d+)\]$/);
          const n = marker && Number(marker[1]);
          return known.has(n) ? (
            <button
              key={i}
              className={open === n ? "cite active" : "cite"}
              title="Show the cited passage"
              onClick={() => toggle(n)}
            >
              {n}
            </button>
          ) : (
            <Fragment key={i}>{part}</Fragment>
          );
        })}
      </p>
      {message.citations.length > 0 ? (
        <ol className="citations">
          {message.citations.map((c) => (
            <li key={c.n} className={open === c.n ? "open" : undefined}>
              <button className="link-button" onClick={() => toggle(c.n)}>
                [{c.n}] {c.section}, {pages(c)}
              </button>
              {open === c.n && <p className="chunk">{c.text}</p>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No passages cited.</p>
      )}
      <p className="muted small answer-meta">
        {message.model} · {message.input_tokens?.toLocaleString()} tokens in, {message.output_tokens?.toLocaleString()}{" "}
        out
      </p>
    </div>
  );
}
