import { useEffect, useState } from "react";
import { api } from "./api.js";

export default function App() {
  const [backend, setBackend] = useState({ state: "checking" });

  useEffect(() => {
    api
      .health()
      .then(() => setBackend({ state: "ok" }))
      .catch((err) => setBackend({ state: "down", error: err.message }));
  }, []);

  return (
    <div className="app">
      <header className="header">
        <h1>AI Research Assistant</h1>
        <span className={`badge badge-${backend.state}`}>
          {backend.state === "ok" && "backend OK"}
          {backend.state === "checking" && "checking backend…"}
          {backend.state === "down" && `backend unreachable: ${backend.error}`}
        </span>
      </header>
      <main className="main">
        <p>Search, library, and AI features arrive in the next milestones.</p>
      </main>
    </div>
  );
}
