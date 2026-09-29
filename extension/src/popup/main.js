import { api, settings, ApiError } from "../api.js";
import * as positions from "./positions.js";
import * as grid from "./grid.js";
import * as journal from "./journal.js";
import * as settingsTab from "./settings.js";
import { status, clearStatus, short } from "./ui.js";

const TABS = { positions, grid, journal, settings: settingsTab };
const view = document.getElementById("view");
const ctx = { me: null, tab: "positions" };

async function loadMe() {
  const { token, apiUrl } = await settings.load();
  ctx.me = null;
  if (token && apiUrl) { try { ctx.me = await api.me(); } catch (e) { if (e.status === 401) await settings.clearToken(); else status(e.message, "err"); } }
  document.getElementById("wallet-chip").textContent = ctx.me?.active_wallet ? short(ctx.me.active_wallet.address) : "not linked";
}

async function rerender() {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b.dataset.tab === ctx.tab);
  if (ctx.tab !== "settings" && !ctx.me) { ctx.tab = "settings"; return rerender(); }
  if (ctx.tab !== "settings" && !ctx.me.active_wallet) status("No wallet yet: send /track to the bot or add an address in Settings.");
  try { await TABS[ctx.tab].render(view, ctx); }
  catch (e) { view.replaceChildren(); status(e.message, "err"); if (e instanceof ApiError && e.status === 401) { await loadMe(); ctx.tab = "settings"; } }
}

ctx.rerender = rerender;
ctx.reload = async () => { await loadMe(); await rerender(); };

document.getElementById("tabs").addEventListener("click", (e) => {
  const t = e.target.dataset?.tab; if (!t) return;
  ctx.tab = t; clearStatus(); rerender();
});
document.getElementById("refresh").addEventListener("click", async () => {
  status("Syncing…");
  try { const r = await api.sync(); status(`Synced ${r.synced} positions`, "ok"); } catch (e) { status(e.message, "err"); }
  await rerender();
});

await loadMe();
if (!ctx.me) ctx.tab = "settings";
await rerender();
