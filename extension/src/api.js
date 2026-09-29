// Клиент общего API (тот же, что использует бот). Токен и адрес API — в chrome.storage.local.
const get = (keys) => new Promise((r) => chrome.storage.local.get(keys, r));
const set = (obj) => new Promise((r) => chrome.storage.local.set(obj, r));

export const settings = {
  load: async () => ({ apiUrl: "", token: "", ...(await get(["apiUrl", "token"])) }),
  save: set,
  clearToken: () => set({ token: "" }),
};

async function request(path, { method = "GET", body, auth = true } = {}) {
  const { apiUrl, token } = await settings.load();
  if (!apiUrl) throw new ApiError("API URL не задан — откройте Settings", 0);
  const headers = { "Content-Type": "application/json" };
  if (auth) { if (!token) throw new ApiError("Расширение не привязано — введите код из /link", 401); headers.Authorization = `Bearer ${token}`; }
  let res;
  try { res = await fetch(apiUrl.replace(/\/+$/, "") + path, { method, headers, body: body ? JSON.stringify(body) : undefined }); }
  catch (e) { throw new ApiError(`Нет связи с API (${e.message})`, 0); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || `HTTP ${res.status}`, res.status);
  return data;
}

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export const api = {
  link: (code) => request("/api/link", { method: "POST", body: { code, label: "chrome-extension" }, auth: false }),
  me: () => request("/api/me"),
  addWallet: (address, label) => request("/api/wallets", { method: "POST", body: { address, label } }),
  activate: (id) => request(`/api/wallets/${id}/activate`, { method: "POST" }),
  positions: (status) => request("/api/positions" + (status ? `?status=${status}` : "")),
  sync: () => request("/api/positions/sync", { method: "POST" }),
  addNote: (positionId, note, pnlUsd) => request(`/api/positions/${positionId}/journal`, { method: "POST", body: { note, pnl_usd: pnlUsd } }),
  journal: () => request("/api/journal/summary"),
};
