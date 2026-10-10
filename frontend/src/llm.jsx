import { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api.js";

// Which LLM the AI features use. The server lists only providers that can answer
// (an API key in .env, or a local Ollama with the model pulled); the choice is
// remembered in this browser.
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
  const [settings, setSettings] = useState({ state: "loading", providers: [], default: null, ollama: null });
  const [chosen, setChosen] = useState(stored);

  // Ollama is detected live, so after `ollama pull` a refresh finds it without a restart.
  function refresh() {
    return api
      .llmSettings()
      .then((s) => setSettings({ state: "ok", ...s }))
      .catch((err) => setSettings({ state: "error", error: err.message, providers: [], default: null, ollama: null }));
  }

  useEffect(() => {
    refresh();
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
    <LlmContext.Provider value={{ settings, provider, current, choose, refresh }}>{children}</LlmContext.Provider>
  );
}

export function useLlm() {
  return useContext(LlmContext);
}

// What to do when no provider can answer. The free path, a local Ollama, comes first.
function setupText(ollama) {
  const model = ollama?.model || "gemma3:4b";
  if (ollama?.running) {
    return `Ollama is running but doesn't have ${model} yet. Run "ollama pull ${model}", then check again. Or add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend.`;
  }
  return `For a free local model, install Ollama from ollama.com and run "ollama pull ${model}", then check again. Or add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend.`;
}

export function AiSetupHint() {
  const { settings, refresh } = useLlm();
  const [checking, setChecking] = useState(false);

  async function check() {
    setChecking(true);
    try {
      await refresh();
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="muted ai-setup">
      <p>No AI model is set up. {setupText(settings.ollama)}</p>
      <button type="button" className="secondary" onClick={check} disabled={checking}>
        {checking ? "Checking…" : "Check again"}
      </button>
    </div>
  );
}

export function ProviderSelect() {
  const { settings, provider, choose } = useLlm();
  if (settings.state === "loading") return null;
  if (!settings.providers.length) {
    return (
      <span className="badge badge-checking" title={setupText(settings.ollama)}>
        AI off: no model set up
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
