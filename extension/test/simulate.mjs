// Проверка calldata против РЕАЛЬНОГО PositionManager через eth_call (ничего не отправляется).
// Запуск: npm run simulate   (нужен интернет; RPC — публичный)
import { ZeroAddress, getAddress } from "ethers";
import { CHAINS, provider, poolState, positionState, simulate } from "../src/core/chain.js";
import { POSM_IFACE, buildClose, buildCollect, buildMintGrid, poolIdOf } from "../src/core/v4.js";
import { buildGrid } from "../src/core/grid.js";
import { findLivePosition } from "./helpers.mjs";

const chain = process.env.CHAIN || "base";
const posm = CHAINS[chain].posm;
const deadline = BigInt(Math.floor(Date.now() / 1000) + 600);
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
  const call = (t, ov) => provider(chain).send("eth_call", [{ from, to: t.to, data: t.data, value: "0x" + t.value.toString(16) }, "latest", ov]);
  try {
    await call(tx, { [from]: { balance: "0x" + (10n ** 20n).toString(16) } });
    check(`mint Grid из ${grid.ranges.length} диапазонов одной tx (single-sided ETH, value=${tx.value})`, true);
  } catch (e) { check("mint Grid одной tx", false, (e.shortMessage || e.message).slice(0, 200)); }
  try {
    await call(tx, { [from]: { balance: "0x0" } });
    check("mint без ETH на балансе ОТКЛОНЯЕТСЯ (контроль)", false, "принят при нулевом балансе!");
  } catch { check("mint без ETH на балансе ОТКЛОНЯЕТСЯ (контроль)", true); }
}
process.exit(failed ? 1 : 0);
