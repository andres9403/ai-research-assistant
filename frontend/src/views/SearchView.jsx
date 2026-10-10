import { useEffect, useState } from "react";
import { api } from "../api.js";
import PaperCard from "../components/PaperCard.jsx";

const PROVIDER_LABELS = { s2: "Semantic Scholar", arxiv: "arXiv" };

export const EMPTY_FILTERS = {
  author: "",
  year_from: "",
  year_to: "",
  venue: "",
  publication_type: "",
  open_access: false,
  sort: "relevance",
};

// Semantic Scholar's publication types; arXiv ignores this filter and says so.
const PUBLICATION_TYPES = [
  ["JournalArticle", "Journal article"],
  ["Conference", "Conference paper"],
  ["Review", "Review"],
  ["MetaAnalysis", "Meta-analysis"],
  ["Dataset", "Dataset"],
  ["Book", "Book"],
  ["BookSection", "Book section"],
];

const SORT_OPTIONS = [
  ["relevance", "Relevance"],
  ["newest", "Newest first"],
  ["citations", "Most cited"],
];

// The filters the API should receive: blanks and defaults are left out, so a
// search with no filters is the plain keyword search.
function activeFilters(filters) {
  return Object.fromEntries(
    Object.entries(filters).filter(([key, value]) => value !== EMPTY_FILTERS[key] && String(value).trim() !== ""),
  );
}

function yearError({ year_from, year_to }) {
  if (year_from && year_to && Number(year_from) > Number(year_to)) {
    return "The start year must not be later than the end year.";
  }
  return null;
}

// `active` is false while another tab is shown; this view stays mounted so the
// results survive, and are rechecked against the library on the way back.
export default function SearchView({ active }) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [state, setState] = useState({ status: "idle" });
  // Per-result save state, keyed by index: "saving" | { id } | { error }
  const [saves, setSaves] = useState({});

  // Papers deleted from the library meanwhile lose their "In library" mark.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    api
      .listPapers()
      .then((papers) => {
        if (cancelled) return;
        const ids = new Set(papers.map((p) => p.id));
        const forget = (r) => (r.saved_id && !ids.has(r.saved_id) ? { ...r, saved_id: null } : r);
        setState((s) => (s.status === "done" ? { ...s, results: s.results.map(forget) } : s));
        setSaves((s) => Object.fromEntries(Object.entries(s).filter(([, v]) => !v?.id || ids.has(v.id))));
      })
      .catch(() => {}); // the marks just stay as they were
    return () => {
      cancelled = true;
    };
  }, [active]);

  async function runSearch(searchFilters) {
    const q = query.trim();
    if (!q) return;
    setState({ status: "loading" });
    setSaves({});
    try {
      setState({ status: "done", ...(await api.search(q, activeFilters(searchFilters))) });
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  }

  function update(field) {
    return (event) => {
      const { type, checked, value } = event.target;
      setFilters((f) => ({ ...f, [field]: type === "checkbox" ? checked : value }));
    };
  }

  // Back to the keyword-only search, rerunning it if results are showing.
  function clearFilters() {
    setFilters(EMPTY_FILTERS);
    if (state.status === "done") runSearch(EMPTY_FILTERS);
  }

  async function save(index, paper) {
    setSaves((s) => ({ ...s, [index]: "saving" }));
    try {
      const { saved_id, ...body } = paper;
      const saved = await api.savePaper(body);
      setSaves((s) => ({ ...s, [index]: { id: saved.id } }));
    } catch (err) {
      const result = err.status === 409 ? { id: err.detail.paper_id } : { error: err.message };
      setSaves((s) => ({ ...s, [index]: result }));
    }
  }

  function saveButton(index, paper) {
    const status = saves[index];
    const savedId = paper.saved_id || status?.id;
    if (savedId) {
      return (
        <a className="saved" href={`#/paper/${savedId}`} title="Open it in your library">
          ✓ In library
        </a>
      );
    }
    return (
      <>
        <button onClick={() => save(index, paper)} disabled={status === "saving"}>
          {status === "saving" ? "Saving…" : "Save"}
        </button>
        {status?.error && <span className="error-inline">{status.error}</span>}
      </>
    );
  }

  const activeCount = Object.keys(activeFilters(filters)).length;
  const invalidYears = yearError(filters);

  return (
    <section>
      <form
        className="search-bar"
        onSubmit={(event) => {
          event.preventDefault();
          runSearch(filters);
        }}
      >
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search papers by keyword or topic, e.g. retrieval augmented generation"
          aria-label="Search papers"
          autoFocus
        />
        <button
          type="button"
          className="secondary"
          aria-expanded={showFilters}
          aria-controls="search-filters"
          onClick={() => setShowFilters((v) => !v)}
        >
          Filters{activeCount > 0 && ` (${activeCount})`}
        </button>
        <button type="submit" disabled={state.status === "loading" || !query.trim() || !!invalidYears}>
          {state.status === "loading" ? "Searching…" : "Search"}
        </button>
      </form>

      {showFilters && (
        <fieldset id="search-filters" className="search-filters">
          <legend>Search filters</legend>
          <label>
            Author
            <input type="text" value={filters.author} onChange={update("author")} placeholder="e.g. Vaswani" />
          </label>
          <label>
            From year
            <input
              type="number"
              min="1900"
              max="2100"
              value={filters.year_from}
              onChange={update("year_from")}
              aria-invalid={!!invalidYears}
              aria-describedby={invalidYears ? "year-error" : undefined}
            />
          </label>
          <label>
            To year
            <input
              type="number"
              min="1900"
              max="2100"
              value={filters.year_to}
              onChange={update("year_to")}
              aria-invalid={!!invalidYears}
              aria-describedby={invalidYears ? "year-error" : undefined}
            />
          </label>
          <label>
            Venue
            <input type="text" value={filters.venue} onChange={update("venue")} placeholder="e.g. Nature" />
          </label>
          <label>
            Paper type
            <select value={filters.publication_type} onChange={update("publication_type")}>
              <option value="">Any type</option>
              {PUBLICATION_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Sort by
            <select value={filters.sort} onChange={update("sort")}>
              {SORT_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={filters.open_access} onChange={update("open_access")} />
            Open-access PDF only
          </label>
          <button type="button" className="secondary" onClick={clearFilters} disabled={activeCount === 0}>
            Clear filters
          </button>
          {invalidYears && (
            <p id="year-error" className="error-inline" role="alert">
              {invalidYears}
            </p>
          )}
          <p className="muted filter-hint">
            Venue must match the name Semantic Scholar uses, such as "Neural Information Processing Systems".
          </p>
        </fieldset>
      )}

      {state.status === "error" && <p className="error">Search failed: {state.error}</p>}
      {state.status === "done" && (
        <>
          <p className="muted">
            {state.results.length} result{state.results.length === 1 ? "" : "s"} from{" "}
            {PROVIDER_LABELS[state.provider]}
            {state.fallback_reason && <> · {state.fallback_reason}</>}
          </p>
          {state.notes?.length > 0 && (
            <ul className="search-notes" aria-label="How the filters were applied">
              {state.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <div className="list">
            {state.results.map((paper, i) => (
              <PaperCard
                key={`${paper.source}-${paper.external_id ?? i}`}
                paper={paper}
                actions={saveButton(i, paper)}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
