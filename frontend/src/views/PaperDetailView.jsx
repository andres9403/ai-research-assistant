import { useEffect, useState } from "react";
import { api, BUSY_STATUSES } from "../api.js";
import { SOURCE_LABELS, formatAuthors } from "../components/PaperCard.jsx";
import PdfStatus from "../components/PdfStatus.jsx";
import SummaryPanel from "../components/SummaryPanel.jsx";
import UploadButton from "../components/UploadButton.jsx";
import { useLlm } from "../llm.jsx";

const METADATA_NOTES = {
  pdf: "Metadata was extracted from the PDF. Check it and edit anything that's wrong.",
  llm: "Metadata was read from the PDF's first page by the AI model. Check it and edit anything that's wrong.",
  semantic_scholar: "Metadata was matched on Semantic Scholar by the paper's identifier.",
  arxiv: "Metadata was matched on arXiv by the paper's identifier.",
  edited: "Metadata edited by you.",
};

// Title-search matches are likely right but not certain, so they get a warning.
const TITLE_MATCH_SITES = { semantic_scholar_title: "Semantic Scholar", arxiv_title: "arXiv" };

export default function PaperDetailView({ id, uploaded }) {
  const [paper, setPaper] = useState(null);
  const [chunks, setChunks] = useState([]);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [pdfAction, setPdfAction] = useState({ status: "idle" });
  const [tick, setTick] = useState(0); // bumping it reloads the paper
  const [tab, setTab] = useState("summary");
  const llm = useLlm();

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getPaper(id), api.listChunks(id)])
      .then(([p, c]) => {
        if (cancelled) return;
        setPaper(p);
        setChunks(c);
      })
      .catch((err) => !cancelled && setError(err));
    return () => {
      cancelled = true;
    };
  }, [id, tick]);

  // Poll while the PDF is downloading or processing.
  const busy = paper && BUSY_STATUSES.includes(paper.status);
  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(() => setTick((t) => t + 1), 2000);
    return () => clearTimeout(timer);
  }, [busy, paper]);

  if (error) {
    return (
      <section>
        <BackLink />
        <p className="error">
          {error.status === 404 ? "This paper is not in your library." : `Could not load paper: ${error.message}`}
        </p>
      </section>
    );
  }
  if (!paper) return <p className="muted">Loading…</p>;

  async function runPdfAction(action) {
    setPdfAction({ status: "working" });
    try {
      setPaper(await action());
      setPdfAction({ status: "idle" });
      setTick((t) => t + 1);
    } catch (err) {
      setPdfAction({ status: "error", error: err.message });
    }
  }

  async function remove() {
    if (!window.confirm(`Remove "${paper.title}" from your library?`)) return;
    try {
      await api.deletePaper(paper.id);
      window.location.hash = "#/library";
    } catch (err) {
      window.alert(`Could not delete: ${err.message}`);
    }
  }

  return (
    <section className="detail">
      <BackLink />
      {uploaded && (
        <p className="banner">
          PDF uploaded and processed locally. Check the metadata below.
        </p>
      )}

      {editing ? (
        <MetadataForm
          paper={paper}
          onCancel={() => setEditing(false)}
          onSaved={(p) => {
            setPaper(p);
            setEditing(false);
          }}
        />
      ) : (
        <div className="panel">
          <div className="card-head">
            <h2 className="detail-title">{paper.title}</h2>
            <div className="card-actions row">
              <button className="secondary" onClick={() => setEditing(true)}>
                Edit metadata
              </button>
              <button className="danger" onClick={remove}>
                Delete
              </button>
            </div>
          </div>
          <p className="card-meta">
            {formatAuthors(paper.authors)}
            {paper.year && <> · {paper.year}</>}
            <span className="tag">{SOURCE_LABELS[paper.source] || paper.source}</span>
          </p>
          <dl className="fields">
            {paper.doi && (
              <>
                <dt>DOI</dt>
                <dd>
                  <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">
                    {paper.doi}
                  </a>
                </dd>
              </>
            )}
            {paper.url && (
              <>
                <dt>Link</dt>
                <dd>
                  <a href={paper.url} target="_blank" rel="noreferrer">
                    {paper.url}
                  </a>
                </dd>
              </>
            )}
          </dl>
          {TITLE_MATCH_SITES[paper.metadata_source] && (
            <p className="warning">
              ⚠ This metadata was filled in from a {TITLE_MATCH_SITES[paper.metadata_source]} search
              for the paper's title, not from an exact identifier, so it may belong to a different
              paper with a similar title. Open the link above to check, and use Edit metadata to fix
              anything that's wrong.
            </p>
          )}
          <h4>Abstract</h4>
          <p className="card-abstract">{paper.abstract || <span className="muted">No abstract.</span>}</p>
          {METADATA_NOTES[paper.metadata_source] && (
            <p className="muted small">{METADATA_NOTES[paper.metadata_source]}</p>
          )}
        </div>
      )}

      <div className="panel">
        <h3>
          PDF <PdfStatus paper={paper} />
        </h3>
        {paper.status_detail && <p className="muted">{paper.status_detail}</p>}
        {paper.status === "no_pdf" && !paper.status_detail && (
          <p className="muted">
            {paper.pdf_url
              ? "An open-access PDF link is known for this paper."
              : "No open-access PDF is known for this paper. Upload one to enable AI features."}
          </p>
        )}
        <div className="row">
          {paper.has_pdf && (
            <a className="button secondary" href={api.pdfUrl(paper.id)} target="_blank" rel="noreferrer">
              View PDF
            </a>
          )}
          {paper.pdf_url && !paper.has_pdf && !busy && (
            <button
              disabled={pdfAction.status === "working"}
              onClick={() => runPdfAction(() => api.fetchPdf(paper.id))}
            >
              Download open-access PDF
            </button>
          )}
          {!busy && (
            <UploadButton
              className={paper.has_pdf ? "secondary" : undefined}
              busy={pdfAction.status === "working"}
              onFile={(file) => runPdfAction(() => api.attachPdf(paper.id, file, llm.provider))}
            >
              {pdfAction.status === "working" ? "Working…" : paper.has_pdf ? "Replace PDF" : "Upload PDF"}
            </UploadButton>
          )}
        </div>
        {pdfAction.status === "error" && <p className="error">{pdfAction.error}</p>}
      </div>

      <div className="panel">
        <div className="subtabs" role="tablist">
          {[
            ["summary", "Summary"],
            ["text", "Extracted text"],
          ].map(([name, label]) => (
            <button
              key={name}
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? "subtab active" : "subtab"}
              onClick={() => setTab(name)}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "summary" ? (
          <SummaryPanel paper={paper} version={tick} />
        ) : chunks.length > 0 ? (
          <FullText paper={paper} chunks={chunks} />
        ) : (
          <p className="muted">No text has been extracted yet. Upload the PDF to see it here.</p>
        )}
      </div>
    </section>
  );
}

function BackLink() {
  return (
    <a className="back" href="#/library">
      ← Library
    </a>
  );
}

// The cleaned text, grouped by section: what the AI features draw on.
function FullText({ paper, chunks }) {
  const sections = [];
  for (const chunk of chunks) {
    const last = sections[sections.length - 1];
    if (last && last.name === chunk.section) {
      last.chunks.push(chunk);
    } else {
      sections.push({ name: chunk.section, chunks: [chunk] });
    }
  }
  const tokens = chunks.reduce((sum, c) => sum + c.n_tokens, 0);

  return (
    <div>
      <p className="muted">
        {paper.page_count} pages → {sections.length} sections, {chunks.length} chunks, about{" "}
        {tokens.toLocaleString()} tokens. Front matter and references are left out.
      </p>
      <div className="sections">
        {sections.map((section, i) => {
          const first = section.chunks[0];
          const last = section.chunks[section.chunks.length - 1];
          const sectionTokens = section.chunks.reduce((sum, c) => sum + c.n_tokens, 0);
          return (
            <details key={i}>
              <summary>
                <span>{section.name}</span>
                <span className="muted small">
                  {first.page_start === last.page_end
                    ? `p. ${first.page_start}`
                    : `pp. ${first.page_start}–${last.page_end}`}{" "}
                  · {section.chunks.length} chunk{section.chunks.length === 1 ? "" : "s"} · ~
                  {sectionTokens} tokens
                </span>
              </summary>
              {section.chunks.map((chunk) => (
                <p key={chunk.id} className="chunk">
                  {chunk.text}
                </p>
              ))}
            </details>
          );
        })}
      </div>
    </div>
  );
}

function MetadataForm({ paper, onCancel, onSaved }) {
  const [form, setForm] = useState({
    title: paper.title,
    authors: (paper.authors || []).join("\n"),
    year: paper.year ?? "",
    doi: paper.doi ?? "",
    url: paper.url ?? "",
    abstract: paper.abstract ?? "",
  });
  const [state, setState] = useState({ status: "idle" });

  function update(field) {
    return (event) => setForm((f) => ({ ...f, [field]: event.target.value }));
  }

  async function save(event) {
    event.preventDefault();
    setState({ status: "saving" });
    try {
      const saved = await api.updatePaper(paper.id, {
        ...form,
        authors: form.authors.split("\n").map((a) => a.trim()).filter(Boolean),
        year: form.year === "" ? null : Number(form.year),
      });
      onSaved(saved);
    } catch (err) {
      setState({ status: "error", error: err.message, paperId: err.detail?.paper_id });
    }
  }

  return (
    <form className="panel form" onSubmit={save}>
      <label>
        Title
        <input value={form.title} onChange={update("title")} required />
      </label>
      <label>
        Authors <span className="muted small">(one per line)</span>
        <textarea value={form.authors} onChange={update("authors")} rows={4} />
      </label>
      <div className="row">
        <label>
          Year
          <input type="number" value={form.year} onChange={update("year")} min={1000} max={2100} />
        </label>
        <label className="grow">
          DOI
          <input value={form.doi} onChange={update("doi")} placeholder="10.xxxx/…" />
        </label>
      </div>
      <label>
        Link
        <input value={form.url} onChange={update("url")} placeholder="https://…" />
      </label>
      <label>
        Abstract
        <textarea value={form.abstract} onChange={update("abstract")} rows={8} />
      </label>
      {state.status === "error" && (
        <p className="error">
          {state.error}
          {state.paperId && (
            <>
              {" "}
              <a href={`#/paper/${state.paperId}`}>Open that paper</a>
            </>
          )}
        </p>
      )}
      <div className="row">
        <button type="submit" disabled={state.status === "saving"}>
          {state.status === "saving" ? "Saving…" : "Save"}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
