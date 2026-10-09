import { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api.js";

// Which LLM the AI features use. The server lists only providers that have an
// API key; the choice is remembered in this browser.
const STORAGE_KEY = "llmProvider";
const LlmContext = createContext(null);

function stored() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function LlmProvider({ children }) {
  const [settings, setSettings] = useState({ state: "loading", providers: [], default: null });
  const [chosen, setChosen] = useState(stored);

  useEffect(() => {
    api
      .llmSettings()
      .then((s) => setSettings({ state: "ok", ...s }))
      .catch((err) => setSettings({ state: "error", error: err.message, providers: [], default: null }));
  }, []);

  function choose(name) {
    setChosen(name);
    try {
      window.localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // Private mode: the choice lasts until the page reloads.
    }
  }

  // A remembered choice whose key has since been removed falls back to the default.
  const names = settings.providers.map((p) => p.name);
  const provider = names.includes(chosen) ? chosen : settings.default;
  const current = settings.providers.find((p) => p.name === provider) || null;

  return (
    <LlmContext.Provider value={{ settings, provider, current, choose }}>{children}</LlmContext.Provider>
  );
}

export function useLlm() {
  return useContext(LlmContext);
}

export function ProviderSelect() {
  const { settings, provider, choose } = useLlm();
  if (settings.state === "loading") return null;
  if (!settings.providers.length) {
    return (
      <span
        className="badge badge-checking"
        title="Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend."
      >
        AI off: no LLM key
      </span>
    );
  }
  return (
    <label className="provider-select">
      <span className="muted small">AI model</span>
      <select value={provider || ""} onChange={(e) => choose(e.target.value)}>
        {settings.providers.map((p) => (
          <option key={p.name} value={p.name}>
            {p.label} ({p.model})
          </option>
        ))}
      </select>
    </label>
  );
}
