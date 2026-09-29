import { formatUnits, parseUnits, getAddress } from "ethers";
import { CHAINS, poolState, balanceOf, permit2Status, simulate } from "../core/chain.js";
import { buildGrid } from "../core/grid.js";
import { orient, toPoolParams, toAssetTerms } from "../core/orient.js";
import { buildMintGrid, ERC20_IFACE, PERMIT2, PERMIT2_IFACE, isNative } from "../core/v4.js";
import { sqrtToPrice } from "../core/tickmath.js";
import { api } from "../api.js";
import { ensureAccount, sendTx, waitReceipt } from "../wallet.js";
import { h, status, confirmModal, short, sig } from "./ui.js";

const DS_CHAIN = { base: "base", ethereum: "ethereum", arbitrum: "arbitrum", bsc: "bnb" };
const MODE_HELP = {
  standard: "Edges at the nearest tick. The nearest range may cover the current price and then needs both tokens.",
  minimize_second: "The nearest range sticks out past the price by at most one tick spacing: the second token is minimal.",
  single_sided: "Every range is strictly on one side of the price: the second token is exactly zero.",
};
const S = { chain: "base", poolId: "", pool: null, preview: null, form: { side: "bids", mode: "single_sided", ranges: 5, from: 2, to: 20, weights: "ascending", amount: "" }, results: [] };

const isPoolId = (s) => /^0x[0-9a-fA-F]{64}$/.test(s.trim());

async function search(q) {
  const r = await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`DexScreener HTTP ${r.status}`);
  const d = await r.json();
  return (d.pairs || []).filter((p) => (p.labels || []).includes("v4") && DS_CHAIN[p.chainId] && /^0x[0-9a-fA-F]{64}$/.test(p.pairAddress))
    .sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0)).slice(0, 8);
}

async function loadPool(ctx) {
  status("Checking the pool on-chain…");
  S.pool = await poolState(S.chain, S.poolId.toLowerCase());
  S.preview = null;
  status("Pool found on-chain", "ok");
  await ctx.rerender();
}

// Токен депозита: bids вносят котировку, asks — актив. Что это за token0/token1 — зависит от ориентации пула.
function depositToken(pool, kind) {
  const o = orient(pool.token0.symbol, pool.token1.symbol);
  const want = kind === "bids" ? o.quote : o.asset;
  return { tok: want === "token0" ? pool.token0 : pool.token1, o };
}
const poolPrice = (p) => sqrtToPrice(p.sqrtPriceX96, p.token0.decimals, p.token1.decimals);
const assetPrice = (p, o) => (o.flipped ? 1 / poolPrice(p) : poolPrice(p));

async function makePreview(ctx) {
  const { pool } = S; const f = S.form;
  const { tok, o } = depositToken(pool, f.side);
  const budget = parseUnits(String(f.amount || "0"), tok.decimals);
  const wallet = ctx.me.active_wallet?.address;
  const pp = toPoolParams(o, f.side, Number(f.from), Number(f.to));
  const grid = buildGrid({ side: pp.side, mode: f.mode, curTick: pool.tick, sqrtCur: pool.sqrtPriceX96, spacing: pool.poolKey.tickSpacing,
    dec0: pool.token0.decimals, dec1: pool.token1.decimals, ranges: Number(f.ranges), fromPct: pp.fromPct, toPct: pp.toPct, weights: f.weights, budget });
  const need = [[pool.token0, grid.totals.amount0Max], [pool.token1, grid.totals.amount1Max]];
  const balances = wallet ? await Promise.all(need.map(([t]) => balanceOf(S.chain, t.address, wallet))) : [];
  const short_ = need.map(([t, n], i) => (balances[i] != null && balances[i] < n ? `${t.symbol}: need ${sig(formatUnits(n, t.decimals))}, wallet has ${sig(formatUnits(balances[i], t.decimals))}` : null)).filter(Boolean);
  const hooked = !isNative(pool.poolKey.hooks);
  if (hooked) grid.warnings.push(`The pool has a hook contract (${short(pool.poolKey.hooks)}): its behaviour is unknown. The simulation before signing shows whether it accepts the position.`);
  S.preview = { grid, balances, short: short_, budget, o };
  status("");
  await ctx.rerender();
}

async function create(ctx) {
  const { pool, preview } = S; const { grid } = preview;
  const wallet = ctx.me.active_wallet?.address;
  const posm = CHAINS[S.chain].posm;
  try {
    await ensureAccount(S.chain, wallet);
    // 1) Approve через Permit2: точные суммы и короткий срок, а не «безлимит»
    for (const [tok, amt] of [[pool.token0, grid.totals.amount0Max], [pool.token1, grid.totals.amount1Max]]) {
      if (tok.native || amt === 0n) continue;
      const st = await permit2Status(S.chain, wallet, tok.address, posm);
      const steps = [];
      if (st.erc20 < amt) steps.push({ label: `Allow Permit2 to spend ${sig(formatUnits(amt, tok.decimals))} ${tok.symbol}`, tx: { to: tok.address, data: ERC20_IFACE.encodeFunctionData("approve", [PERMIT2, amt]) } });
      const exp = BigInt(Math.floor(Date.now() / 1000) + 3600);
      if (st.permit2Amount < amt || st.permit2Expiration < Number(exp) - 1800) steps.push({ label: `Allow Position Manager to use ${tok.symbol} via Permit2 (1 hour)`, tx: { to: PERMIT2, data: PERMIT2_IFACE.encodeFunctionData("approve", [tok.address, posm, amt, exp]) } });
      for (const s of steps) {
        if (!(await confirmModal({ title: "Approval", rows: [["Step", s.label], ["Wallet", short(wallet)]], confirmText: "Approve via wallet" }))) return status("Cancelled");
        status("Confirm the approval in your wallet…");
        const hash = await sendTx(S.chain, wallet, s.tx);
        const rc = await waitReceipt(S.chain, hash);
        if (rc.status !== 1) throw new Error(`Approval откатился: ${hash}`);
      }
    }
    // 2) Mint всего Grid одной транзакцией, NFT сразу на основной кошелёк
    const tx = buildMintGrid({ posm, poolKey: pool.poolKey, ranges: grid.ranges, recipient: wallet, deadline: BigInt(Math.floor(Date.now() / 1000) + 1200) });
    const sim = await simulate(S.chain, tx, wallet);
    const f = (v, t) => `${sig(formatUnits(v, t.decimals))} ${t.symbol}`;
    const go = await confirmModal({ title: `Create Grid: ${grid.ranges.length} ranges`, confirmText: "Create via wallet",
      rows: [["Pool", `${pool.token0.symbol}/${pool.token1.symbol} (${pool.poolKey.fee / 10000}%)`], ["Mode", `${S.form.side} · ${S.form.mode}`],
        ["Max spend", `${f(grid.totals.amount0Max, pool.token0)} + ${f(grid.totals.amount1Max, pool.token1)}`], ["NFTs go to", short(wallet)],
        ["Simulation", sim.ok ? `OK, gas ≈ ${sim.gas}` : "FAILED", sim.ok ? "pos" : "neg"]],
      warnings: grid.warnings, disabledReason: sim.ok ? "" : sim.error });
    if (!go) return status("Cancelled");
    status("Confirm the transaction in your wallet…");
    const hash = await sendTx(S.chain, wallet, tx);
    status(`Sent ${short(hash)}, waiting…`);
    const rc = await waitReceipt(S.chain, hash);
    if (rc.status !== 1) throw new Error(`Транзакция откатилась: ${hash}`);
    status(`Grid created (${short(hash)}). Positions appear after the next sync.`, "ok");
    S.preview = null;
    await api.sync().catch(() => {});
    await ctx.rerender();
  } catch (e) { status(e.message, "err"); }
}

export async function render(view, ctx) {
  const f = S.form;
  const inp = (key, props = {}) => h("input", { value: f[key], ...props, oninput: (e) => { f[key] = e.target.value; S.preview = null; } });
  const sel = (key, opts) => h("select", { onchange: (e) => { f[key] = e.target.value; S.preview = null; ctx.rerender(); } },
    opts.map(([v, t]) => h("option", { value: v, text: t, selected: f[key] === v })));

  const chainSel = h("select", { onchange: (e) => { S.chain = e.target.value; S.pool = null; S.preview = null; } },
    Object.entries(CHAINS).map(([k, c]) => h("option", { value: k, text: c.name, selected: S.chain === k })));
  const q = h("input", { placeholder: "Pool ID (0x…64 hex) or token address / symbol", value: S.poolId });
  const resultsBox = h("div", {}, S.results.map((p) => h("div", { class: "card", style: "cursor:pointer;padding:6px 8px;margin:6px 0", onclick: () => {
    S.chain = DS_CHAIN[p.chainId]; S.poolId = p.pairAddress; S.results = []; loadPool(ctx).catch((e) => status(e.message, "err"));
  } }, h("span", { class: "pair", text: `${p.baseToken.symbol}/${p.quoteToken.symbol}` }), " ",
    h("span", { class: "sub", text: `${p.chainId} · liq $${Math.round(p.liquidity?.usd || 0).toLocaleString("en-US")} · ${short(p.pairAddress)}` }))));
  const find = h("button", { class: "btn fit", text: "Find", onclick: async () => {
    const v = q.value.trim(); S.poolId = v;
    try {
      if (isPoolId(v)) { S.results = []; await loadPool(ctx); }
      else { status("Searching v4 pools…"); S.results = await search(v); status(S.results.length ? "" : "No Uniswap v4 pools found"); await ctx.rerender(); }
    } catch (e) { status(e.message, "err"); }
  } });

  const els = [h("div", { class: "row" }, h("div", { class: "fit", style: "width:110px" }, chainSel), q, find), resultsBox];

  if (S.pool) {
    const p = S.pool;
    const { tok: prim, o } = depositToken(p, f.side);
    els.push(h("div", { class: "card", style: "margin-top:10px" },
      h("div", { class: "pair", text: `${o.assetSym} / ${o.quoteSym}` }),
      h("div", { class: "sub", text: `fee ${p.poolKey.fee / 10000}% · spacing ${p.poolKey.tickSpacing} · price ${sig(assetPrice(p, o))} ${o.quoteSym} per ${o.assetSym}${o.flipped ? " (pool stores the inverse: token order is by address)" : ""}` }),
      h("div", { class: "sub", text: `pool ${short(p.poolId)} · hooks ${isNative(p.poolKey.hooks) ? "none" : short(p.poolKey.hooks)}` })));
    els.push(
      h("label", { text: "Side" }), sel("side", [["bids", `Bids: buy ${o.assetSym} below the price, deposit ${o.quoteSym}`], ["asks", `Asks: sell ${o.assetSym} above the price, deposit ${o.assetSym}`]]),
      h("label", { text: "Mode" }), sel("mode", [["standard", "Standard"], ["minimize_second", "Minimize second token"], ["single_sided", "Single-sided"]]),
      h("div", { class: "hint", text: MODE_HELP[f.mode] }),
      h("div", { class: "row" },
        h("div", {}, h("label", { text: "Ranges" }), inp("ranges", { type: "number", min: 1, max: 50 })),
        h("div", {}, h("label", { text: `From, % of ${o.assetSym} price` }), inp("from", { type: "number", min: 0, step: "any" })),
        h("div", {}, h("label", { text: "To, %" }), inp("to", { type: "number", min: 0, step: "any" }))),
      h("div", { class: "row" },
        h("div", {}, h("label", { text: "Capital split" }), sel("weights", [["equal", "Equal"], ["ascending", "More to far ranges"], ["descending", "More to near ranges"]])),
        h("div", {}, h("label", { text: `Amount, ${prim.symbol}` }), inp("amount", { type: "number", min: 0, step: "any", placeholder: "0.0" }))),
      h("div", { class: "actions" }, h("button", { class: "btn", text: "Preview", onclick: () => makePreview(ctx).catch((e) => status(e.message, "err")) })));
    if (S.preview) {
      const { grid, short: lacking, o: po } = S.preview;
      const fmt = (v, t) => sig(formatUnits(v, t.decimals));
      const curAsset = assetPrice(p, po);
      els.push(h("table", { style: "margin-top:10px" },
        h("thead", {}, h("tr", {}, ...["#", `${po.assetSym} price, ${po.quoteSym}`, "% from now", p.token0.symbol, p.token1.symbol].map((t) => h("th", { text: t })))),
        h("tbody", {}, grid.ranges.map((r, i) => { const t = toAssetTerms(po, r, poolPrice(p)); return h("tr", {},
          h("td", { text: i + 1 }), h("td", { text: `${sig(t.lo, 4)} – ${sig(t.hi, 4)}` }),
          h("td", { text: `${t.lowPct.toFixed(1)}…${t.highPct.toFixed(1)}` }), h("td", { text: fmt(r.amount0, p.token0) }), h("td", { text: fmt(r.amount1, p.token1) })); }),
          h("tr", {}, h("td", { text: "Σ" }), h("td", { text: `now ${sig(curAsset, 4)}` }), h("td"), h("td", { text: fmt(grid.totals.amount0, p.token0) }), h("td", { text: fmt(grid.totals.amount1, p.token1) })))),
        ...grid.warnings.map((w) => h("div", { class: "warn", text: w })),
        ...lacking.map((w) => h("div", { class: "warn neg", text: `Not enough funds — ${w}` })),
        h("div", { class: "hint", text: "One transaction creates all ranges; the NFTs go to your active wallet. The wallet will simulate it again before you sign." }),
        h("div", { class: "actions" }, h("button", { class: "btn", text: "Create Grid", disabled: lacking.length > 0, onclick: () => create(ctx) })));
    }
  }
  view.replaceChildren(...els);
}
