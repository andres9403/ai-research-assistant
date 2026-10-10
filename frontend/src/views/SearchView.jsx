import { useEffect, useState } from "react";
import { api } from "../api.js";
import PaperCard from "../components/PaperCard.jsx";

const PROVIDER_LABELS = { s2: "Semantic Scholar", arxiv: "arXiv" };

// `active` is false while another tab is shown; this view stays mounted so the
// results survive, and are rechecked against the library on the way back.
export default function SearchView({ active }) {
  const [query, setQuery] = useState("");
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

  async function runSearch(event) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setState({ status: "loading" });
    setSaves({});
    try {
      setState({ status: "done", ...(await api.search(q)) });
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
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

  return (
    <section>
      <form className="search-bar" onSubmit={runSearch}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search papers by keyword or topic, e.g. retrieval augmented generation"
          aria-label="Search papers"
          autoFocus
        />
        <button type="submit" disabled={state.status === "loading" || !query.trim()}>
          {state.status === "loading" ? "Searching…" : "Search"}
        </button>
      </form>

      {state.status === "error" && <p className="error">Search failed: {state.error}</p>}
      {state.status === "done" && (
        <>
          <p className="muted">
            {state.results.length} result{state.results.length === 1 ? "" : "s"} from{" "}
            {PROVIDER_LABELS[state.provider]}
            {state.fallback_reason && <> · {state.fallback_reason}</>}
          </p>
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
