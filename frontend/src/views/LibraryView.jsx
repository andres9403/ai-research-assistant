import { useEffect, useState } from "react";
import { api, BUSY_STATUSES } from "../api.js";
import PaperCard from "../components/PaperCard.jsx";
import UploadButton from "../components/UploadButton.jsx";
import { useLlm } from "../llm.jsx";

const EMPTY_FILTERS = { q: "", source: "", year_from: "", year_to: "" };

export default function LibraryView() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [state, setState] = useState({ status: "loading", papers: [] });
  const [upload, setUpload] = useState({ status: "idle" });
  const [tick, setTick] = useState(0); // bumping it reloads the list
  const llm = useLlm();

  // While any PDF is downloading or processing, reload every 2 s to show progress.
  const busy = state.papers.some((p) => BUSY_STATUSES.includes(p.status));
  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(() => setTick((t) => t + 1), 2000);
    return () => clearTimeout(timer);
  }, [busy, state.papers]);

  useEffect(() => {
    let cancelled = false;
    // Debounce typing so each keystroke doesn't hit the API.
    const timer = setTimeout(() => {
      api
        .listPapers(filters)
        .then((papers) => !cancelled && setState({ status: "done", papers, filters }))
        .catch((err) => !cancelled && setState((s) => ({ ...s, status: "error", error: err.message })));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters, tick]);

  function update(field) {
    return (event) => setFilters((f) => ({ ...f, [field]: event.target.value }));
  }

  async function uploadPdf(file) {
    setUpload({ status: "uploading", name: file.name });
    try {
      const paper = await api.uploadPdf(file, llm.provider);
      window.location.hash = `#/paper/${paper.id}?uploaded`;
    } catch (err) {
      setUpload({ status: "error", error: err.message, paperId: err.detail?.paper_id });
    }
  }

  async function remove(paper) {
    if (!window.confirm(`Remove "${paper.title}" from your library?`)) return;
    try {
      await api.deletePaper(paper.id);
      setState((s) => ({ ...s, papers: s.papers.filter((p) => p.id !== paper.id) }));
    } catch (err) {
      window.alert(`Could not delete: ${err.message}`);
    }
  }

  const filtered = Object.values(filters).some((v) => v !== "");
  const { papers } = state;
  // While typing, the list still shows the previous filters' papers.
  const current = state.status === "done" && state.filters === filters;

  return (
    <section>
      <div className="toolbar">
        <UploadButton onFile={uploadPdf} busy={upload.status === "uploading"}>
          {upload.status === "uploading" ? `Extracting ${upload.name}…` : "Upload PDF"}
        </UploadButton>
        <span className="muted">
          {llm.provider
            ? "Text is extracted locally. Only page one's text is sent to the AI model, to read the title and authors."
            : "Text and metadata are extracted locally; the PDF never leaves your machine."}
        </span>
      </div>
      {upload.status === "error" && (
        <p className="error">
          Upload failed: {upload.error}
          {upload.paperId && (
            <>
              {" "}
              <a href={`#/paper/${upload.paperId}`}>Open the existing paper</a>
            </>
          )}
        </p>
      )}
      <div className="filters">
        <input
          type="search"
          value={filters.q}
          onChange={update("q")}
          placeholder="Filter by title, author or abstract"
          aria-label="Filter library"
        />
        <select value={filters.source} onChange={update("source")} aria-label="Source">
          <option value="">All sources</option>
          <option value="s2">Semantic Scholar</option>
          <option value="arxiv">arXiv</option>
          <option value="upload">Upload</option>
        </select>
        <input
          type="number"
          value={filters.year_from}
          onChange={update("year_from")}
          placeholder="From year"
          aria-label="From year"
        />
        <input
          type="number"
          value={filters.year_to}
          onChange={update("year_to")}
          placeholder="To year"
          aria-label="To year"
        />
        {filtered && (
          <button className="secondary" onClick={() => setFilters(EMPTY_FILTERS)}>
            Clear
          </button>
        )}
      </div>

      {state.status === "error" && <p className="error">Could not load library: {state.error}</p>}
      {state.status === "done" && (
        <p className={current ? "muted" : "muted invisible"}>
          {papers.length} paper{papers.length === 1 ? "" : "s"}
          {filtered ? ` match${papers.length === 1 ? "es" : ""} your filters` : " in your library"}
        </p>
      )}
      {current && !papers.length && !filtered && (
        <p className="empty">
          Your library is empty. <a href="#/search">Search for papers</a> to save them here, or
          upload a PDF.
        </p>
      )}
      <div className="list">
        {papers.map((paper) => (
          <PaperCard
            key={paper.id}
            paper={paper}
            href={`#/paper/${paper.id}`}
            actions={
              <button className="danger" onClick={() => remove(paper)}>
                Delete
              </button>
            }
          />
        ))}
      </div>
    </section>
  );
}
