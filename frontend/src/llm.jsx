import { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api.js";

// Which LLM the AI features use. The server lists only providers that can answer
// (an API key, from .env or entered here, or a local Ollama with the model
// pulled); the choice is remembered in this browser. API keys typed here, for
// the LLMs and for Semantic Scholar search, go straight to the backend, which
// saves them to .env; they are never stored in the browser or sent back.
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
  // Whether a Semantic Scholar key is set; null until known.
  const [searchKeySet, setSearchKeySet] = useState(null);
  // The API keys menu, which the search page opens when a key would help.
  const [keysMenu, setKeysMenu] = useState({ open: false, provider: null, opened: 0 });

  // Ollama is detected live, so after `ollama pull` a refresh finds it without a restart.
  function refresh() {
    api
      .searchSettings()
      .then((s) => setSearchKeySet(s.semantic_scholar_key))
      .catch(() => {}); // search works without a key, so this is only a menu detail
    return api
      .llmSettings()
      .then((s) => setSettings({ state: "ok", ...s }))
      .catch((err) => setSettings({ state: "error", error: err.message, providers: [], default: null, ollama: null }));
  }

  useEffect(() => {
    refresh();
  }, []);

  // A newly added LLM key is what the user wants to use, so it becomes the choice.
  async function saveKey(name, key) {
    if (name === SEARCH_KEY) {
      setSearchKeySet((await api.setSearchKey(name, key)).semantic_scholar_key);
      return;
    }
    const s = await api.setApiKey(name, key);
    setSettings({ state: "ok", ...s });
    choose(name);
  }

  async function removeKey(name) {
    if (name === SEARCH_KEY) {
      setSearchKeySet((await api.removeSearchKey(name)).semantic_scholar_key);
      return;
    }
    const s = await api.removeApiKey(name);
    setSettings({ state: "ok", ...s });
  }

  // Open the API keys menu with `provider` chosen in its form.
  function openKeys(provider) {
    setKeysMenu((m) => ({ open: true, provider, opened: m.opened + 1 }));
  }

  function setKeysOpen(open) {
    setKeysMenu((m) => (m.open === open ? m : { ...m, open }));
  }

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
    <LlmContext.Provider
      value={{
        settings,
        provider,
        current,
        choose,
        refresh,
        saveKey,
        removeKey,
        searchKeySet,
        keysMenu,
        openKeys,
        setKeysOpen,
      }}
    >
      {children}
    </LlmContext.Provider>
  );
}

export function useLlm() {
  return useContext(LlmContext);
}

const KEY_PROVIDERS = [
  ["anthropic", "Anthropic Claude"],
  ["openai", "OpenAI"],
];
// Optional: it raises Semantic Scholar's rate limit, so searches fall back to arXiv less often.
const SEARCH_KEY = "semantic_scholar";
const ALL_KEY_PROVIDERS = [...KEY_PROVIDERS, [SEARCH_KEY, "Semantic Scholar (search)"]];
const S2_KEY_FORM = "https://www.semanticscholar.org/product/api#api-key-form";

// What to do when no provider can answer: paste a key, or run a free local model.
function setupText(ollama) {
  const model = ollama?.model || "gemma3:4b";
  const local = ollama?.running
    ? `Ollama is running but doesn't have ${model} yet: run "ollama pull ${model}", then check again.`
    : `For a free local model instead, install Ollama from ollama.com, run "ollama pull ${model}", then check again.`;
  return `Paste an Anthropic or OpenAI API key. ${local}`;
}

// `providers` lists the keys the form offers; `initial` picks the first one shown.
export function ApiKeyForm({ providers = KEY_PROVIDERS, initial = null, autoFocus = false }) {
  const { saveKey } = useLlm();
  const [name, setName] = useState(initial || providers[0][0]);
  const [key, setKey] = useState("");
  const [state, setState] = useState({ status: "idle" });
  const saving = state.status === "saving";

  async function submit(e) {
    e.preventDefault();
    setState({ status: "saving" });
    try {
      await saveKey(name, key);
      setKey("");
      setState({ status: "saved", name });
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  }

  return (
    <form className="key-form" onSubmit={submit}>
      <div className="key-row">
        <select value={name} onChange={(e) => setName(e.target.value)} aria-label="Provider">
          {providers.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste API key"
          aria-label="API key"
          autoFocus={autoFocus}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button type="submit" disabled={saving || !key.trim()}>
          {saving ? "Checking…" : "Save key"}
        </button>
      </div>
      {state.status === "error" && <p className="error small">{state.error}</p>}
      {state.status === "saved" && (
        <p className="small">
          {state.name === SEARCH_KEY
            ? "Key saved. Searches and upload lookups now use it."
            : "Key saved. The AI features are on."}
        </p>
      )}
      {name === SEARCH_KEY && (
        <p className="muted small">
          Optional. Semantic Scholar approves keys after you{" "}
          <a href={S2_KEY_FORM} target="_blank" rel="noreferrer">
            request one
          </a>
          , which can take a while; search works without one meanwhile.
        </p>
      )}
      <p className="muted small">
        The key is checked with the provider, then saved in .env on your computer. It is never shown again or kept
        in the browser.
      </p>
    </form>
  );
}

// Header menu to add, replace or remove keys, whatever is already set up.
export function ApiKeysMenu() {
  const { settings, removeKey, searchKeySet, keysMenu, setKeysOpen } = useLlm();
  const [error, setError] = useState(null);
  if (settings.state !== "ok") return null;
  const keyed = ALL_KEY_PROVIDERS.filter(([name]) =>
    name === SEARCH_KEY ? searchKeySet : settings.providers.some((p) => p.name === name),
  );

  async function remove(name, label) {
    if (!window.confirm(`Remove the ${label} API key from .env?`)) return;
    try {
      setError(null);
      await removeKey(name);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <details className="keys-menu" open={keysMenu.open} onToggle={(e) => setKeysOpen(e.currentTarget.open)}>
      <summary>API keys</summary>
      <div className="keys-panel">
        {keyed.length > 0 && (
          <ul className="keys-set">
            {keyed.map(([name, label]) => (
              <li key={name}>
                <span>{label}: key set</span>
                <button type="button" className="link-button" onClick={() => remove(name, label)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="error small">{error}</p>}
        {/* Opening the menu from a link remounts the form with that link's provider chosen. */}
        <ApiKeyForm
          key={keysMenu.opened}
          providers={ALL_KEY_PROVIDERS}
          initial={keysMenu.provider}
          autoFocus={keysMenu.opened > 0}
        />
      </div>
    </details>
  );
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
      <ApiKeyForm />
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
