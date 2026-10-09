import { useEffect, useState } from "react";
import { api } from "./api.js";
import { LlmProvider, ProviderSelect } from "./llm.jsx";
import LibraryView from "./views/LibraryView.jsx";
import PaperDetailView from "./views/PaperDetailView.jsx";
import SearchView from "./views/SearchView.jsx";

const VIEWS = { search: "Search", library: "Library" };

// The view lives in the URL hash so a page refresh keeps you where you were:
// #/search, #/library, or #/paper/<id> (with "?uploaded" right after an upload).
function currentRoute() {
  const path = window.location.hash.replace(/^#\/?/, "");
  const paper = path.match(/^paper\/(\d+)(\?uploaded)?$/);
  if (paper) return { view: "paper", paperId: Number(paper[1]), uploaded: !!paper[2] };
  return { view: VIEWS[path] ? path : "search" };
}

export default function App() {
  const [backend, setBackend] = useState({ state: "checking" });
  const [route, setRoute] = useState(currentRoute);

  useEffect(() => {
    api
      .health()
      .then(() => setBackend({ state: "ok" }))
      .catch((err) => setBackend({ state: "down", error: err.message }));
  }, []);

  useEffect(() => {
    const onHashChange = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  // The paper detail page belongs to the Library tab.
  const activeTab = route.view === "paper" ? "library" : route.view;

  return (
    <LlmProvider>
      <div className="app">
        <header className="header">
          <h1>AI Research Assistant</h1>
          <nav className="tabs">
            {Object.entries(VIEWS).map(([name, label]) => (
              <a key={name} href={`#/${name}`} className={name === activeTab ? "tab active" : "tab"}>
                {label}
              </a>
            ))}
          </nav>
          <ProviderSelect />
          <span className={`badge badge-${backend.state}`}>
            {backend.state === "ok" && "backend OK"}
            {backend.state === "checking" && "checking backend…"}
            {backend.state === "down" && `backend unreachable: ${backend.error}`}
          </span>
        </header>
        <main className="main">
          {route.view === "paper" ? (
            <PaperDetailView key={route.paperId} id={route.paperId} uploaded={route.uploaded} />
          ) : route.view === "library" ? (
            <LibraryView />
          ) : (
            <SearchView />
          )}
        </main>
      </div>
    </LlmProvider>
  );
}
