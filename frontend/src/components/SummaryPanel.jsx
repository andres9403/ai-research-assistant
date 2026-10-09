import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLlm } from "../llm.jsx";

const FIELDS = [
  ["problem", "Problem"],
  ["approach", "Approach"],
  ["data", "Data"],
  ["results", "Results"],
  ["limitations", "Limitations"],
];

function pages(s) {
  return s.page_start === s.page_end ? `p. ${s.page_start}` : `pp. ${s.page_start}–${s.page_end}`;
}

// `version` changes whenever the paper is reloaded (e.g. after a new PDF), which
// may have dropped the cached summary on the server.
export default function SummaryPanel({ paper, version }) {
  const llm = useLlm();
  const [summary, setSummary] = useState({ status: "loading" });
  const [action, setAction] = useState({ status: "idle" });

  useEffect(() => {
    let cancelled = false;
    api
      .getSummary(paper.id)
      .then((s) => !cancelled && setSummary({ status: "ok", data: s }))
      .catch((err) =>
        !cancelled && setSummary(err.status === 404 ? { status: "none" } : { status: "error", error: err.message })
      );
    return () => {
      cancelled = true;
    };
  }, [paper.id, version]);

  async function generate(refresh) {
    setAction({ status: "working" });
    try {
      const data = await api.createSummary(paper.id, { provider: llm.provider, refresh });
      setSummary({ status: "ok", data });
      setAction({ status: "idle" });
    } catch (err) {
      setAction({ status: "error", error: err.message });
    }
  }

  const ready = paper.status === "ready";
  const working = action.status === "working";
  const model = llm.current;

  if (summary.status === "loading") return <p className="muted">Loading…</p>;
  if (summary.status === "error") return <p className="error">Could not load the summary: {summary.error}</p>;

  const data = summary.status === "ok" ? summary.data : null;
  const canGenerate = ready && model && !working;

  return (
    <div>
      {!ready && (
        <p className="muted">
          Summaries are written from the paper's full text, which isn't available yet. Upload the PDF to
          enable AI features.
        </p>
      )}
      {ready && !model && (
        <p className="muted">
          No AI model is set up. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend.
        </p>
      )}

      {data ? (
        <div className="summary">
          <p className="tldr">
            <strong>TL;DR</strong> {data.content.tldr}
          </p>
          <dl className="summary-fields">
            {FIELDS.map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{data.content[key]}</dd>
              </div>
            ))}
          </dl>
          <details className="summary-sources muted small">
            <summary>
              Written by {data.model} from {data.sections.length} section
              {data.sections.length === 1 ? "" : "s"} of the paper · {data.input_tokens.toLocaleString()} tokens
              in, {data.output_tokens.toLocaleString()} out · {new Date(data.created_at).toLocaleString()}
            </summary>
            <p>{data.sections.map((s) => `${s.section} (${pages(s)})`).join(" · ")}</p>
          </details>
        </div>
      ) : (
        ready &&
        model && (
          <p className="muted">
            The summary is written from the paper's key sections (about 8k tokens at most), not just its
            abstract. It's saved, so it's generated only once.
          </p>
        )
      )}

      {ready && model && (
        <div className="row">
          <button
            className={data ? "secondary" : undefined}
            disabled={!canGenerate}
            onClick={() => generate(Boolean(data))}
          >
            {working
              ? `Summarizing with ${model.model}…`
              : data
                ? data.model === model.model
                  ? "Regenerate"
                  : `Regenerate with ${model.model}`
                : "Generate summary"}
          </button>
        </div>
      )}
      {action.status === "error" && <p className="error">{action.error}</p>}
    </div>
  );
}
