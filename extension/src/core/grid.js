// LP Grid: раскладка диапазонов и распределение капитала. Чистая математика, без сети.
//
// Определения режимов (собственные — публичного описания Liquidity Ladder нет):
//   side "below": bid-лесенка под ценой, «основной» токен = token1 (цена = token1/token0)
//   side "above": ask-лесенка над ценой, «основной» токен = token0
//   standard        — края диапазонов по ближайшему тику; ближний диапазон может накрыть текущую цену
//                     и тогда потребует второй токен в естественной пропорции.
//   minimize_second — как standard, но ближний диапазон не выходит за цену больше чем на 1 tickSpacing:
//                     второго токена нужно минимум (только если полоса начинается у цены).
//   single_sided    — все диапазоны строго по одну сторону цены, второй токен = 0.
import { Q96, sqrtRatioAtTick, tickToPrice, floorTick, ceilTick, clampTick } from "./tickmath.js";
import { amountsForLiquidity } from "./liquidity.js";

const LN = Math.log(1.0001);
export const MODES = ["standard", "minimize_second", "single_sided"];
const roundTick = (t, s) => Math.round(t / s) * s;

function weightsFor(kind, n) {
  if (Array.isArray(kind)) {
    if (kind.length !== n || kind.some((w) => !(w > 0))) throw new Error("weights: нужен массив положительных чисел длиной N");
    return kind;
  }
  return Array.from({ length: n }, (_, i) =>
    kind === "ascending" ? i + 1 : kind === "descending" ? n - i : 1);
}

// ликвидность из бюджета основного токена
function liquidityFromPrimary(side, sqrtCur, curTick, tickLower, tickUpper, budget) {
  const sL = sqrtRatioAtTick(tickLower), sU = sqrtRatioAtTick(tickUpper);
  if (side === "below") {
    if (tickLower > curTick) return 0n;                       // весь диапазон над ценой: нужен только token0
    const top = curTick >= tickUpper ? sU : sqrtCur;          // token1-часть: от sL до min(sU, sqrtCur)
    return (budget * Q96) / (top - sL);
  }
  if (tickUpper <= curTick) return 0n;                        // весь диапазон под ценой: нужен только token1
  const bot = curTick < tickLower ? sL : sqrtCur;             // token0-часть: от max(sL, sqrtCur) до sU
  return (budget * sU * bot) / (Q96 * (sU - bot));
}

export function buildGrid(p) {
  const { side, mode, sqrtCur, curTick, spacing, dec0, dec1, weights = "equal", slippageBps = 100 } = p;
  const fromPct = p.fromPct ?? 0, toPct = p.toPct;
  let n = p.ranges;
  const budget = BigInt(p.budget);
  const warnings = [];

  if (!["below", "above"].includes(side)) throw new Error("side: below | above");
  if (!MODES.includes(mode)) throw new Error(`mode: ${MODES.join(" | ")}`);
  if (!Number.isInteger(n) || n < 1 || n > 50) throw new Error("ranges: целое от 1 до 50");
  if (!(fromPct >= 0 && toPct > fromPct)) throw new Error("нужно 0 <= from < to");
  if (side === "below" && toPct >= 100) throw new Error("для bid-лесенки to < 100%");
  if (budget <= 0n) throw new Error("budget должен быть > 0");

  const cf = floorTick(curTick, spacing);
  // below: tick = cur + ln(1 - pct/100)/LN ; above: tick = cur + ln(1 + pct/100)/LN
  const rawTick = (pct) => curTick + Math.log(side === "below" ? 1 - pct / 100 : 1 + pct / 100) / LN;

  let bot, top;
  if (side === "below") {
    const edgeTop = rawTick(fromPct);
    if (mode === "standard") top = roundTick(edgeTop, spacing);
    else if (mode === "minimize_second") top = roundTick(edgeTop, spacing) <= cf ? roundTick(edgeTop, spacing) : cf + spacing;
    else top = Math.min(floorTick(edgeTop, spacing), cf);
    bot = floorTick(rawTick(toPct), spacing);
  } else {
    const edgeBot = rawTick(fromPct);
    if (mode === "standard") bot = roundTick(edgeBot, spacing);
    else if (mode === "minimize_second") bot = roundTick(edgeBot, spacing) >= cf + spacing ? roundTick(edgeBot, spacing) : cf;
    else bot = Math.max(ceilTick(edgeBot, spacing), cf + spacing);
    top = ceilTick(rawTick(toPct), spacing);
  }
  bot = clampTick(bot, spacing); top = clampTick(top, spacing);

  const total = Math.floor((top - bot) / spacing);
  if (total < 1) throw new Error("полоса уже одного tickSpacing: увеличьте to или уменьшите from");
  if (total < n) { warnings.push(`Полоса вмещает только ${total} диапазонов из ${n} (tickSpacing=${spacing})`); n = total; }

  // сегменты: остаток отдаётся дальним (они шире)
  const base = Math.floor(total / n), extra = total % n;
  const widths = Array.from({ length: n }, (_, i) => base + (i >= n - extra ? 1 : 0)); // i=0 — ближний к цене
  const edges = [];
  if (side === "below") { let hi = top; for (const w of widths) { edges.push([hi - w * spacing, hi]); hi -= w * spacing; } }
  else { let lo = bot; for (const w of widths) { edges.push([lo, lo + w * spacing]); lo += w * spacing; } }

  const ws = weightsFor(weights, n);
  const wsum = ws.reduce((a, b) => a + b, 0);
  // целочисленные веса: доли считаются от их суммы, поэтому в сумме не превышают бюджет
  const wInt = ws.map((w) => BigInt(Math.round(w * 1e6)));
  const wIntSum = wInt.reduce((a, b) => a + b, 0n);
  let ranges = edges.map(([tickLower, tickUpper], i) => {
    const share = (budget * wInt[i]) / wIntSum;
    const L = liquidityFromPrimary(side, sqrtCur, curTick, tickLower, tickUpper, share);
    const need = amountsForLiquidity(sqrtCur, sqrtRatioAtTick(tickLower), sqrtRatioAtTick(tickUpper), L, true);
    const priceLower = tickToPrice(tickLower, dec0, dec1), priceUpper = tickToPrice(tickUpper, dec0, dec1);
    const nearTick = side === "below" ? tickUpper : tickLower;
    const farTick = side === "below" ? tickLower : tickUpper;
    return {
      index: i, tickLower, tickUpper, priceLower, priceUpper, liquidity: L,
      amount0: need.amount0, amount1: need.amount1,
      amount0Max: need.amount0 + (need.amount0 * BigInt(slippageBps)) / 10000n + (need.amount0 > 0n ? 1n : 0n),
      amount1Max: need.amount1 + (need.amount1 * BigInt(slippageBps)) / 10000n + (need.amount1 > 0n ? 1n : 0n),
      weightPct: (ws[i] / wsum) * 100,
      nearPct: (Math.pow(1.0001, nearTick - curTick) - 1) * 100,
      farPct: (Math.pow(1.0001, farTick - curTick) - 1) * 100,
      straddles: tickLower <= curTick && curTick < tickUpper,
    };
  });
  const dead = ranges.filter((r) => r.liquidity === 0n);
  if (dead.length) { warnings.push(`Диапазонов с нулевой ликвидностью: ${dead.length} — они не будут созданы`); ranges = ranges.filter((r) => r.liquidity > 0n); }
  if (!ranges.length) throw new Error("ни один диапазон не получил ликвидность: проверьте сторону и бюджет");

  const totals = ranges.reduce((t, r) => ({ amount0: t.amount0 + r.amount0, amount1: t.amount1 + r.amount1,
    amount0Max: t.amount0Max + r.amount0Max, amount1Max: t.amount1Max + r.amount1Max }),
    { amount0: 0n, amount1: 0n, amount0Max: 0n, amount1Max: 0n });
  const secondToken = side === "below" ? totals.amount0 : totals.amount1;
  if (mode === "single_sided" && secondToken > 0n) warnings.push("Внутренняя ошибка: single_sided потребовал второй токен");
  if (mode !== "single_sided" && secondToken > 0n) warnings.push("Нужен и второй токен — см. итоги превью");
  return { side, mode, ranges, totals, secondToken, warnings, primary: side === "below" ? "token1" : "token0" };
}
