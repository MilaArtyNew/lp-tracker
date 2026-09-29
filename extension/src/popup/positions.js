import { api } from "../api.js";
import { CHAINS, positionState, tokenInfo, simulate } from "../core/chain.js";
import { buildClose, buildCollect } from "../core/v4.js";
import { ensureAccount, sendTx, waitReceipt } from "../wallet.js";
import { formatUnits } from "ethers";
import { h, status, confirmModal, short, usd, sig } from "./ui.js";

let showClosed = false;
const SLIPPAGE_BPS = 100n; // 1% на выводимый капитал при закрытии (комиссии в min не входят)

function rangeBar(p) {
  const lo = Number(p.price_lower), hi = Number(p.price_upper), cur = Number(p.current_price);
  const inRange = cur >= lo && cur < hi;
  const pct = hi > lo ? Math.min(100, Math.max(0, ((cur - lo) / (hi - lo)) * 100)) : 0;
  return h("div", {},
    h("div", { class: `bar ${inRange ? "" : "out"}` }, h("div", { class: "fill" }), h("div", { class: "mark", style: `left:calc(${pct}% - 1px)` })),
    h("div", { class: "nums" }, h("span", { text: sig(lo) }), h("span", { text: `now ${sig(cur)}` }), h("span", { text: sig(hi) })));
}

function card(p, ctx) {
  const open = p.status === "open";
  const inRange = p.in_range;
  const chainName = CHAINS[p.chain]?.name || p.chain;
  const note = h("textarea", { rows: 2, placeholder: "Note for this position" });
  const pnl = h("input", { type: "number", step: "any", placeholder: "Result, $ (e.g. sold on GMGN)" });
  const noteBox = h("div", { hidden: true },
    note, h("div", { class: "row", style: "margin-top:6px" }, pnl,
      h("button", { class: "btn fit", text: "Save", onclick: async () => {
        try { await api.addNote(p.id, note.value || null, pnl.value === "" ? null : Number(pnl.value)); status("Saved to journal", "ok"); note.value = ""; pnl.value = ""; noteBox.hidden = true; }
        catch (e) { status(e.message, "err"); }
      } })));
  const v4 = p.protocol === "v4";
  return h("div", { class: "card" },
    h("div", { class: "top" },
      h("div", {}, h("span", { class: "pair", text: `${p.symbol0}/${p.symbol1}` }), " ",
        h("span", { class: "sub", text: `${chainName} · ${p.protocol} · #${p.token_id}` })),
      open ? h("span", { class: `badge ${inRange ? "in" : "out"}`, text: inRange ? "in range" : "out of range" }) : h("span", { class: "badge", text: "closed" })),
    open && rangeBar(p),
    h("div", { class: "nums" }, h("span", { text: `Value ${usd(p.value_usd)}` }), h("span", { text: `Fees ${usd(p.fees_usd)}` })),
    h("div", { class: "actions" },
      open && h("button", { class: "btn", text: "Collect fees", disabled: !v4, title: v4 ? "" : "v3: use the DEX site", onclick: () => act(p, "collect", ctx) }),
      open && h("button", { class: "btn danger", text: "Close", disabled: !v4, title: v4 ? "" : "v3: use the DEX site", onclick: () => act(p, "close", ctx) }),
      h("button", { class: "btn secondary", text: "Journal", onclick: () => { noteBox.hidden = !noteBox.hidden; } })),
    noteBox);
}

async function act(p, kind, ctx) {
  const chain = p.chain;
  const wallet = ctx.me.active_wallet?.address;
  try {
    status("Reading position on-chain…");
    const tokenId = BigInt(p.token_id);
    const st = await positionState(chain, tokenId);
    if (st.owner.toLowerCase() !== wallet.toLowerCase()) throw new Error(`NFT принадлежит ${short(st.owner)}, а активный кошелёк — ${short(wallet)}.`);
    const deadline = BigInt(Math.floor(Date.now() / 1000) + 1200);
    const base = { posm: CHAINS[chain].posm, tokenId, currency0: st.poolKey.currency0, currency1: st.poolKey.currency1, recipient: wallet, deadline };
    const tx = kind === "close"
      ? buildClose({ ...base, amount0Min: st.expected.amount0 - (st.expected.amount0 * SLIPPAGE_BPS) / 10000n, amount1Min: st.expected.amount1 - (st.expected.amount1 * SLIPPAGE_BPS) / 10000n })
      : buildCollect(base);
    const [t0, t1, sim] = await Promise.all([tokenInfo(chain, st.poolKey.currency0), tokenInfo(chain, st.poolKey.currency1), simulate(chain, tx, wallet)]);
    const f = (v, t) => `${sig(formatUnits(v, t.decimals))} ${t.symbol}`;
    const rows = [
      ["Position", `${t0.symbol}/${t1.symbol} #${p.token_id} (${CHAINS[chain].name})`],
      ["Recipient", short(wallet)],
      ...(kind === "close" ? [["You receive (≥, w/o fees)", `${f(st.expected.amount0, t0)} + ${f(st.expected.amount1, t1)}`], ["Then", "NFT is burned"]]
                           : [["You receive", "accrued fees of both tokens"]]),
      ["Simulation", sim.ok ? `OK, gas ≈ ${sim.gas}` : "FAILED", sim.ok ? "pos" : "neg"],
    ];
    status("");
    const go = await confirmModal({ title: kind === "close" ? "Close position" : "Collect fees", rows, danger: kind === "close",
      confirmText: kind === "close" ? "Close via wallet" : "Collect via wallet", disabledReason: sim.ok ? "" : sim.error });
    if (!go) return;
    await ensureAccount(chain, wallet);
    status("Confirm in your wallet…");
    const hash = await sendTx(chain, wallet, tx);
    status(`Sent ${short(hash)}, waiting for confirmation…`);
    const rc = await waitReceipt(chain, hash);
    if (rc.status !== 1) throw new Error(`Транзакция откатилась: ${hash}`);
    status(`Done: ${kind === "close" ? "position closed" : "fees collected"} (${short(hash)})`, "ok");
    await api.sync().catch(() => {});
    await ctx.rerender();
  } catch (e) { status(e.message, "err"); }
}

export async function render(view, ctx) {
  const data = await api.positions(showClosed ? undefined : "open");
  const list = data.positions.filter((p) => showClosed || p.status === "open");
  const open = list.filter((p) => p.status === "open");
  view.replaceChildren(
    h("div", { class: "row", style: "margin-bottom:8px" },
      h("div", { class: "sub", text: `${open.length} open · value ${usd(open.reduce((s, p) => s + Number(p.value_usd || 0), 0))} · fees ${usd(open.reduce((s, p) => s + Number(p.fees_usd || 0), 0))}` }),
      h("label", { class: "fit", style: "margin:0;display:flex;gap:4px;align-items:center" },
        h("input", { type: "checkbox", checked: showClosed, style: "width:auto", onchange: (e) => { showClosed = e.target.checked; ctx.rerender(); } }), "closed")),
    ...(list.length ? list.map((p) => card(p, ctx)) : [h("div", { class: "empty", text: "No positions yet. They appear within ~2 minutes after the wallet is linked." })]));
}
