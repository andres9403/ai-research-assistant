import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiKeysMenu, LlmProvider, useLlm } from "./llm.jsx";
import { api } from "./api.js";

vi.mock("./api.js", () => ({
  api: {
    llmSettings: vi.fn(),
    setApiKey: vi.fn(),
    removeApiKey: vi.fn(),
    searchSettings: vi.fn(),
    setSearchKey: vi.fn(),
    removeSearchKey: vi.fn(),
  },
}));

const S2_KEY = "S2abcdefghijklmnopqrstuvwxyz0123456789AB";
const LLM_SETTINGS = { default: null, providers: [], ollama: { model: "gemma3:4b", running: false } };

// Stands in for the search page's "Add a Semantic Scholar API key" link.
function OpenFromSearch() {
  const { openKeys } = useLlm();
  return (
    <button type="button" onClick={() => openKeys("semantic_scholar")}>
      hint
    </button>
  );
}

function renderMenu() {
  render(
    <LlmProvider>
      <OpenFromSearch />
      <ApiKeysMenu />
    </LlmProvider>,
  );
}

beforeEach(() => {
  api.llmSettings.mockResolvedValue(LLM_SETTINGS);
  api.searchSettings.mockResolvedValue({ semantic_scholar_key: false });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("API keys menu: Semantic Scholar", () => {
  it("opens from the search hint with Semantic Scholar chosen, and saves the key through the backend only", async () => {
    api.setSearchKey.mockResolvedValue({ semantic_scholar_key: true });
    const user = userEvent.setup();
    renderMenu();
    await screen.findByText("API keys");

    await user.click(screen.getByRole("button", { name: "hint" }));
    expect(screen.getByText("API keys").closest("details").open).toBe(true);
    expect(screen.getByLabelText("Provider").value).toBe("semantic_scholar");
    expect(document.activeElement).toBe(screen.getByLabelText("API key"));

    await user.type(screen.getByLabelText("API key"), S2_KEY);
    await user.click(screen.getByRole("button", { name: "Save key" }));

    expect(api.setSearchKey).toHaveBeenCalledWith("semantic_scholar", S2_KEY);
    expect(api.setApiKey).not.toHaveBeenCalled();
    expect(await screen.findByText(/Searches and upload lookups now use it/)).toBeTruthy();
    expect(screen.getByLabelText("API key").value).toBe("");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(S2_KEY);
    expect(screen.getByText("Semantic Scholar (search): key set")).toBeTruthy();
  });

  it("shows a rejected key's error and keeps it unsaved", async () => {
    api.setSearchKey.mockRejectedValue(new Error("Semantic Scholar rejected this API key."));
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "hint" }));
    await user.type(screen.getByLabelText("API key"), S2_KEY);
    await user.click(screen.getByRole("button", { name: "Save key" }));

    expect(await screen.findByText("Semantic Scholar rejected this API key.")).toBeTruthy();
    expect(screen.queryByText(/key set/)).toBeNull();
  });

  it("removes a saved key", async () => {
    api.searchSettings.mockResolvedValue({ semantic_scholar_key: true });
    api.removeSearchKey.mockResolvedValue({ semantic_scholar_key: false });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderMenu();

    const row = (await screen.findByText("Semantic Scholar (search): key set")).closest("li");
    await user.click(within(row).getByRole("button", { name: "Remove" }));

    expect(api.removeSearchKey).toHaveBeenCalledWith("semantic_scholar");
    expect(api.removeApiKey).not.toHaveBeenCalled();
    expect(screen.queryByText(/key set/)).toBeNull();
  });
});
