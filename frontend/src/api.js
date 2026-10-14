async function request(path, options) {
  const res = await fetch(`/api${path}`, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
const post = (path, body) =>
  request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  servers: () => request("/servers"),
  metrics: (id, limit = 60) => request(`/servers/${id}/metrics?limit=${limit}`),
  processes: (id) => request(`/servers/${id}/processes?top=8`),
  logs: (id) => request(`/servers/${id}/logs?limit=30`),
  incidents: (status) => request(`/incidents${status ? `?status=${status}` : ""}`),
  incident: (id) => request(`/incidents/${id}`),
  analyze: (id) => post(`/incidents/${id}/analyze`),
  setStatus: (id, status) => post(`/incidents/${id}/status`, { status }),
  decide: (actionId, approve) => post(`/actions/${actionId}/decision`, { approve, decided_by: "admin" }),
};

export const timeOf = (ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const pct = (v) => (v == null ? "–" : `${Math.round(v)}%`);
