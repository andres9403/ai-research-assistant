export class ApiError extends Error {
  constructor(status, detail) {
    const message = typeof detail === "string" ? detail : detail?.message;
    super(message || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  const resp = await fetch(`/api${path}`, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    // FastAPI validation errors arrive as a list of {msg} objects.
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => d.msg).join("; ")
      : body.detail;
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  return resp.status === 204 ? null : resp.json();
}

function query(params) {
  const entries = Object.entries(params).filter(([, v]) => v !== "" && v != null);
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
}

export const api = {
  health: () => request("/health"),
  search: (q, limit = 20) => request(`/search${query({ q, limit })}`),
  listPapers: (filters = {}) => request(`/papers${query(filters)}`),
  savePaper: (paper) =>
    request("/papers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(paper),
    }),
  deletePaper: (id) => request(`/papers/${id}`, { method: "DELETE" }),
  getPaper: (id) => request(`/papers/${id}`),
  updatePaper: (id, changes) =>
    request(`/papers/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }),
  listChunks: (id) => request(`/papers/${id}/chunks`),
  uploadPdf: (file, provider) =>
    request("/papers/upload", { method: "POST", body: pdfForm(file, { provider }) }),
  attachPdf: (id, file) => request(`/papers/${id}/pdf`, { method: "POST", body: pdfForm(file) }),
  fetchPdf: (id) => request(`/papers/${id}/pdf/fetch`, { method: "POST" }),
  pdfUrl: (id) => `/api/papers/${id}/pdf`,
  llmSettings: () => request("/settings/llm"),
  getSummary: (id) => request(`/papers/${id}/summary`),
  createSummary: (id, { provider, refresh = false } = {}) =>
    request(`/papers/${id}/summary${query({ provider, refresh: refresh || null })}`, { method: "POST" }),
};

function pdfForm(file, fields = {}) {
  const form = new FormData();
  form.append("file", file);
  for (const [name, value] of Object.entries(fields)) {
    if (value != null) form.append(name, value);
  }
  return form;
}

// Papers in these states change on the server; views poll until they settle.
export const BUSY_STATUSES = ["downloading", "processing"];
