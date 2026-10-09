async function request(path, options = {}) {
  const resp = await fetch(`/api${path}`, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.status === 204 ? null : resp.json();
}

export const api = {
  health: () => request("/health"),
};
