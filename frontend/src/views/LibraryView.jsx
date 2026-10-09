import { useEffect, useState } from "react";
import { api } from "../api.js";
import PaperCard from "../components/PaperCard.jsx";

const EMPTY_FILTERS = { q: "", source: "", year_from: "", year_to: "" };

export default function LibraryView() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [state, setState] = useState({ status: "loading", papers: [] });

  useEffect(() => {
    let cancelled = false;
    // Debounce typing so each keystroke doesn't hit the API.
    const timer = setTimeout(() => {
      api
        .listPapers(filters)
        .then((papers) => !cancelled && setState({ status: "done", papers }))
        .catch((err) => !cancelled && setState((s) => ({ ...s, status: "error", error: err.message })));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters]);

  function update(field) {
    return (event) => setFilters((f) => ({ ...f, [field]: event.target.value }));
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

  return (
    <section>
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
        <p className="muted">
          {papers.length} paper{papers.length === 1 ? "" : "s"}
          {filtered ? " match your filters" : " in your library"}
        </p>
      )}
      {state.status === "done" && !papers.length && !filtered && (
        <p className="empty">
          Your library is empty. <a href="#/search">Search for papers</a> and save them here.
        </p>
      )}
      <div className="list">
        {papers.map((paper) => (
          <PaperCard
            key={paper.id}
            paper={paper}
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
