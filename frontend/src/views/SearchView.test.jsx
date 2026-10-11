import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchView from "./SearchView.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({ api: { search: vi.fn(), listPapers: vi.fn(), savePaper: vi.fn() } }));
const openKeys = vi.fn();
vi.mock("../llm.jsx", () => ({ useLlm: () => ({ openKeys }) }));

const RESPONSE = {
  provider: "arxiv",
  fallback_reason: "Semantic Scholar unavailable (HTTP 429)",
  unapplied_filters: ["venue"],
  notes: ["arXiv can't filter by venue, so that filter was not applied."],
  results: [{ source: "arxiv", external_id: "1", title: "A paper", authors: ["Ashish Vaswani"], year: 2017 }],
};

beforeEach(() => {
  api.search.mockResolvedValue(RESPONSE);
  api.listPapers.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openFilters(user) {
  render(<SearchView active />);
  await user.type(screen.getByRole("searchbox", { name: "Search papers" }), "attention");
  await user.click(screen.getByRole("button", { name: "Filters" }));
  return within(screen.getByRole("group", { name: "Search filters" }));
}

describe("SearchView filters", () => {
  it("searches by keyword only when no filters are set", async () => {
    const user = userEvent.setup();
    render(<SearchView active />);
    await user.type(screen.getByRole("searchbox", { name: "Search papers" }), "attention{Enter}");
    expect(api.search).toHaveBeenCalledWith("attention", {});
  });

  it("sends the chosen filters and shows how they were applied", async () => {
    const user = userEvent.setup();
    const filters = await openFilters(user);
    await user.type(filters.getByLabelText("Author"), "Vaswani");
    await user.type(filters.getByLabelText("From year"), "2017");
    await user.type(filters.getByLabelText("To year"), "2018");
    await user.type(filters.getByLabelText("Venue"), "NeurIPS");
    await user.selectOptions(filters.getByLabelText("Paper type"), "Conference");
    await user.selectOptions(filters.getByLabelText("Sort by"), "newest");
    await user.click(filters.getByLabelText("Open-access PDF only"));
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(api.search).toHaveBeenCalledWith("attention", {
      author: "Vaswani",
      year_from: "2017",
      year_to: "2018",
      venue: "NeurIPS",
      publication_type: "Conference",
      open_access: true,
      sort: "newest",
    });
    expect(screen.getByRole("button", { name: "Filters (7)" })).toBeTruthy();
    expect(await screen.findByText(/from arXiv · Semantic Scholar unavailable/)).toBeTruthy();
    const notes = screen.getByRole("list", { name: "How the filters were applied" });
    expect(notes.textContent).toBe("arXiv can't filter by venue, so that filter was not applied.");
  });

  it("clears every filter and reruns the keyword-only search", async () => {
    const user = userEvent.setup();
    const filters = await openFilters(user);
    const clear = filters.getByRole("button", { name: "Clear filters" });
    expect(clear.disabled).toBe(true);
    await user.type(filters.getByLabelText("Author"), "Vaswani");
    await user.selectOptions(filters.getByLabelText("Sort by"), "citations");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(api.search).toHaveBeenLastCalledWith("attention", { author: "Vaswani", sort: "citations" });
    await screen.findByText("A paper");

    await user.click(clear);
    expect(api.search).toHaveBeenLastCalledWith("attention", {});
    expect(filters.getByLabelText("Author").value).toBe("");
    expect(filters.getByLabelText("Sort by").value).toBe("relevance");
    expect(clear.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Filters" })).toBeTruthy();
  });

  it("blocks a search whose year range is reversed", async () => {
    const user = userEvent.setup();
    const filters = await openFilters(user);
    await user.type(filters.getByLabelText("From year"), "2020");
    await user.type(filters.getByLabelText("To year"), "2010");

    expect(screen.getByRole("alert").textContent).toMatch(/start year must not be later/);
    expect(filters.getByLabelText("From year").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("button", { name: "Search" }).disabled).toBe(true);
    await user.type(screen.getByRole("searchbox", { name: "Search papers" }), "{Enter}");
    expect(api.search).not.toHaveBeenCalled();
  });
});

describe("SearchView Semantic Scholar key hint", () => {
  async function search(user) {
    render(<SearchView active />);
    await user.type(screen.getByRole("searchbox", { name: "Search papers" }), "attention{Enter}");
    await screen.findByText("A paper");
  }

  it("offers the API keys menu when a keyless search was rate-limited", async () => {
    api.search.mockResolvedValue({ ...RESPONSE, suggest_s2_key: true });
    const user = userEvent.setup();
    await search(user);
    await user.click(screen.getByRole("button", { name: "Add a Semantic Scholar API key" }));
    expect(openKeys).toHaveBeenCalledWith("semantic_scholar");
  });

  it("stays quiet when a key would not help", async () => {
    const user = userEvent.setup();
    await search(user);
    expect(screen.queryByRole("button", { name: "Add a Semantic Scholar API key" })).toBeNull();
  });
});
