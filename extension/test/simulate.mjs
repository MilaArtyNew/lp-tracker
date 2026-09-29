// Проверка calldata против РЕАЛЬНОГО PositionManager через eth_call (ничего не отправляется).
// Запуск: npm run simulate   (нужен интернет; RPC — публичный)
import { ZeroAddress, getAddress } from "ethers";
import { CHAINS, provider, poolState, positionState, simulate } from "../src/core/chain.js";
import { POSM_IFACE, buildClose, buildCollect, buildMintGrid, poolIdOf } from "../src/core/v4.js";
import { buildGrid } from "../src/core/grid.js";
import { findLivePosition } from "./helpers.mjs";
import { orient, toPoolParams } from "../src/core/orient.js";
import { JsonRpcProvider, keccak256, zeroPadValue, toBeHex, getAddress as gaddr, concat } from "ethers";
import { PERMIT2 } from "../src/core/v4.js";

const chain = process.env.CHAIN || "base";
const RPC = { base: "https://base-rpc.publicnode.com" };                 // mainnet.base.org даёт 429 при серии eth_call
const raw = (c) => new JsonRpcProvider(RPC[c] || CHAINS[c].rpc[0], CHAINS[c].chainId, { staticNetwork: true });
const posm = CHAINS[chain].posm;
const deadline = BigInt(Math.floor(Date.now() / 1000) + 600);
const from0 = "0x" + "22".repeat(20);
let failed = 0;
const check = (name, ok, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name} ${extra}`); if (!ok) failed++; };

// 1) close / collect с адреса реального владельца свежей позиции
const found = await findLivePosition(chain);
if (!found) { console.log("SKIP  не нашла живую позицию среди 2500 NFT возрастом от ~6 ч"); }
else {
  const { id, st } = found;
  console.log(`позиция #${id} владелец ${st.owner} L=${st.liquidity} ticks [${st.tickLower},${st.tickUpper}] curTick=${st.slot0.tick}`);
  const args = { posm, tokenId: id, currency0: st.poolKey.currency0, currency1: st.poolKey.currency1, recipient: st.owner, deadline };
  const collect = await simulate(chain, buildCollect(args), st.owner);
  check("collect (DECREASE 0 + TAKE_PAIR) с адреса владельца", collect.ok, collect.ok ? `gas≈${collect.gas}` : collect.error);
  const close = await simulate(chain, buildClose({ ...args,
    amount0Min: (st.expected.amount0 * 99n) / 100n, amount1Min: (st.expected.amount1 * 99n) / 100n }), st.owner);
  check("close (BURN + TAKE_PAIR) с min = 99% ожидаемого", close.ok, close.ok ? `gas≈${close.gas}` : close.error);
  const stranger = "0x" + "11".repeat(20);
  const foreign = await simulate(chain, buildClose({ ...args, recipient: stranger }), stranger);
  check("close с чужого адреса ОТКЛОНЯЕТСЯ (контроль: тест не всегда-зелёный)", !foreign.ok, foreign.ok ? "" : foreign.error.slice(0, 60));
  const tooHigh = await simulate(chain, buildClose({ ...args, amount0Min: 10n ** 30n, amount1Min: 10n ** 30n }), st.owner);
  check("close с завышенным min ОТКЛОНЯЕТСЯ (защита от слиппеджа работает)", !tooHigh.ok);
}

// 2) mint Grid: ищем инициализированный пул native/USDC, баланс ETH подменяем state override
const USDC = { base: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", ethereum: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" }[chain];
let pool = null;
for (const [fee, tickSpacing] of [[500, 10], [3000, 60], [100, 1], [10000, 200]]) {
  try { pool = await poolState(chain, poolIdOf({ currency0: ZeroAddress, currency1: USDC, fee, tickSpacing, hooks: ZeroAddress })); break; } catch { /* нет пула */ }
}
if (!pool) console.log("SKIP  пул native/USDC не найден");
else {
  console.log(`пул fee=${pool.poolKey.fee} spacing=${pool.poolKey.tickSpacing} tick=${pool.tick}`);
  const from = "0x" + "22".repeat(20);
  const grid = buildGrid({ side: "above", mode: "single_sided", curTick: pool.tick, sqrtCur: pool.sqrtPriceX96,
    spacing: pool.poolKey.tickSpacing, dec0: 18, dec1: 6, ranges: 5, fromPct: 3, toPct: 30, budget: 10n ** 18n, weights: "ascending" });
  const tx = buildMintGrid({ posm, poolKey: pool.poolKey, ranges: grid.ranges, recipient: from, deadline });
  const call = (t, ov) => raw(chain).send("eth_call", [{ from, to: t.to, data: t.data, value: "0x" + t.value.toString(16) }, "latest", ov]);
  try {
    await call(tx, { [from]: { balance: "0x" + (10n ** 20n).toString(16) } });
    check(`mint Grid из ${grid.ranges.length} диапазонов одной tx (single-sided ETH, value=${tx.value})`, true);
  } catch (e) { check("mint Grid одной tx", false, (e.shortMessage || e.message).slice(0, 200)); }
  try {
    await call(tx, { [from]: { balance: "0x0" } });
    check("mint без ETH на балансе ОТКЛОНЯЕТСЯ (контроль)", false, "принят при нулевом балансе!");
  } catch { check("mint без ETH на балансе ОТКЛОНЯЕТСЯ (контроль)", true); }
}

// 3) Grid в терминах актива на РЕАЛЬНЫХ пулах: перевёрнутый (нативный ETH = токен0) и ERC20-депозит через Permit2
const slotOf = (owner, idx) => keccak256(concat([zeroPadValue(owner, 32), zeroPadValue(toBeHex(idx), 32)]));
async function findSlot(prov, token, selector, args, slotFn, marker = 12345n) {
  for (let i = 0; i < 20; i++) {
    const data = selector + args.map((a) => zeroPadValue(a, 32).slice(2)).join("");
    try { const r = await prov.send("eth_call", [{ to: token, data }, "latest", { [token]: { stateDiff: { [slotFn(i)]: zeroPadValue(toBeHex(marker), 32) } } }]); if (BigInt(r) === marker) return slotFn(i); } catch { /* другой слот */ }
  }
  return null;
}
const POOLS3 = [
  { chain: "robinhood", id: "0x6d9af55c4cb0d740dc5237e8fe2d60e017412fb1af4e2d81e948124797e56748", kind: "bids", title: "Robinhood CRCL/ETH — bids (перевёрнутый: ETH = токен0, депозит нативный ETH)" },
  { chain: "robinhood", id: "0x6d9af55c4cb0d740dc5237e8fe2d60e017412fb1af4e2d81e948124797e56748", kind: "asks", title: "Robinhood CRCL/ETH — asks (депозит CRCL, ERC20)" },
  { chain: "base", id: "0xb24f7fcd75c3a001b74be4c60f181c024154c4d7c33097777e3950979042b073", kind: "bids", title: "Base tBTC/USDC — bids (депозит USDC, ERC20 через Permit2)" },
];
for (const P of POOLS3) {
  console.log(`\n== ${P.title}`);
  const pool = await poolState(P.chain, P.id);
  const o = orient(pool.token0.symbol, pool.token1.symbol);
  const pp = toPoolParams(o, P.kind, 5, 30);
  const dep = (P.kind === "bids" ? o.quote : o.asset) === "token0" ? pool.token0 : pool.token1;
  const budget = 100n * 10n ** BigInt(dep.decimals) / (dep.native ? 1000n : 1n);       // 0.1 ETH или 100 токенов
  const grid = buildGrid({ side: pp.side, mode: "single_sided", curTick: pool.tick, sqrtCur: pool.sqrtPriceX96, spacing: pool.poolKey.tickSpacing,
    dec0: pool.token0.decimals, dec1: pool.token1.decimals, ranges: 4, fromPct: pp.fromPct, toPct: pp.toPct, budget, weights: "equal" });
  const posmAddr = CHAINS[P.chain].posm, tx = buildMintGrid({ posm: posmAddr, poolKey: pool.poolKey, ranges: grid.ranges, recipient: from0, deadline });
  const prov = raw(P.chain), ov = {};
  ov[from0] = { balance: "0x" + (10n ** 22n).toString(16) };
  const erc = [[pool.token0, grid.totals.amount0Max], [pool.token1, grid.totals.amount1Max]].filter(([t, n]) => !t.native && n > 0n);
  let skip = false;
  for (const [t] of erc) {
    const bal = await findSlot(prov, t.address, "0x70a08231", [from0], (i) => slotOf(from0, i));
    const alw = await findSlot(prov, t.address, "0xdd62ed3e", [from0, PERMIT2], (i) => keccak256(concat([zeroPadValue(PERMIT2, 32), slotOf(from0, i)])));
    let p2 = null;
    for (let i = 0; i < 8 && !p2; i++) {
      const slot = keccak256(concat([zeroPadValue(posmAddr, 32), keccak256(concat([zeroPadValue(t.address, 32), slotOf(from0, i)]))]));
      const packed = (2n ** 160n - 1n) | (BigInt(Math.floor(Date.now() / 1000) + 86400) << 160n);
      const data = "0x927da105" + [from0, t.address, posmAddr].map((a) => zeroPadValue(a, 32).slice(2)).join("");
      try { const r = await prov.send("eth_call", [{ to: PERMIT2, data }, "latest", { [PERMIT2]: { stateDiff: { [slot]: zeroPadValue(toBeHex(packed), 32) } } }]); if (BigInt("0x" + r.slice(2, 66)) === 2n ** 160n - 1n) p2 = { slot, packed }; } catch { /* другой слот */ }
    }
    if (!bal || !alw || !p2) { console.log(`SKIP  не нашла слоты ${t.symbol} (bal=${!!bal} alw=${!!alw} p2=${!!p2})`); skip = true; break; }
    ov[t.address] = { stateDiff: { [bal]: zeroPadValue(toBeHex(10n ** 30n), 32), [alw]: zeroPadValue(toBeHex(10n ** 30n), 32) } };
    ov[PERMIT2] = { stateDiff: { ...(ov[PERMIT2]?.stateDiff || {}), [p2.slot]: zeroPadValue(toBeHex(p2.packed), 32) } };
  }
  if (skip) continue;
  const call = (o2) => prov.send("eth_call", [{ from: from0, to: tx.to, data: tx.data, value: "0x" + tx.value.toString(16) }, "latest", o2]);
  try { await call(ov); check(`Grid ${P.kind}, ${grid.ranges.length} диапазонов, депозит ${dep.symbol}: принят PositionManager`, true, `value=${tx.value}; сторона пула=${pp.side}, flipped=${o.flipped}`); }
  catch (e) { check(`Grid ${P.kind}: принят PositionManager`, false, (e.shortMessage || e.message).slice(0, 180)); continue; }
  try { await call({ [from0]: { balance: "0x0" } }); check(`Grid ${P.kind}: без средств ОТКЛОНЯЕТСЯ (контроль)`, false, "принят без средств!"); }
  catch { check(`Grid ${P.kind}: без средств ОТКЛОНЯЕТСЯ (контроль)`, true); }
}
process.exit(failed ? 1 : 0);
