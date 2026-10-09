import { useEffect, useState } from "react";
import { api } from "./api.js";
import LibraryView from "./views/LibraryView.jsx";
import SearchView from "./views/SearchView.jsx";

const VIEWS = {
  search: { label: "Search", component: SearchView },
  library: { label: "Library", component: LibraryView },
};

// The view lives in the URL hash so a page refresh keeps you where you were.
function currentView() {
  const name = window.location.hash.replace(/^#\/?/, "");
  return VIEWS[name] ? name : "search";
}

export default function App() {
  const [backend, setBackend] = useState({ state: "checking" });
  const [view, setView] = useState(currentView);

  useEffect(() => {
    api
      .health()
      .then(() => setBackend({ state: "ok" }))
      .catch((err) => setBackend({ state: "down", error: err.message }));
  }, []);

  useEffect(() => {
    const onHashChange = () => setView(currentView());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const View = VIEWS[view].component;

  return (
    <div className="app">
      <header className="header">
        <h1>AI Research Assistant</h1>
        <nav className="tabs">
          {Object.entries(VIEWS).map(([name, { label }]) => (
            <a key={name} href={`#/${name}`} className={name === view ? "tab active" : "tab"}>
              {label}
            </a>
          ))}
        </nav>
        <span className={`badge badge-${backend.state}`}>
          {backend.state === "ok" && "backend OK"}
          {backend.state === "checking" && "checking backend…"}
          {backend.state === "down" && `backend unreachable: ${backend.error}`}
        </span>
      </header>
      <main className="main">
        <View />
      </main>
    </div>
  );
}
