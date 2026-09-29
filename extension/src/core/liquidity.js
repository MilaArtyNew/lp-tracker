// Формулы Uniswap v3/v4 для количества токенов и ликвидности. Всё в BigInt, округление как в контракте:
// токены, которые нужно ВНЕСТИ, округляются вверх; которые получим при выводе — вниз.
import { Q96 } from "./tickmath.js";

const mulDivUp = (a, b, d) => (a * b + d - 1n) / d;

export function amount0ForLiquidity(sqrtA, sqrtB, L, roundUp = false) {
  if (sqrtA > sqrtB) [sqrtA, sqrtB] = [sqrtB, sqrtA];
  const num = L * Q96 * (sqrtB - sqrtA);
  const den = sqrtB * sqrtA;
  return roundUp ? mulDivUp(num, 1n, den) : num / den;
}

export function amount1ForLiquidity(sqrtA, sqrtB, L, roundUp = false) {
  if (sqrtA > sqrtB) [sqrtA, sqrtB] = [sqrtB, sqrtA];
  const num = L * (sqrtB - sqrtA);
  return roundUp ? mulDivUp(num, 1n, Q96) : num / Q96;
}

// сколько токенов нужно для ликвидности L в диапазоне [sqrtL, sqrtU] при цене sqrtCur
export function amountsForLiquidity(sqrtCur, sqrtL, sqrtU, L, roundUp = true) {
  if (sqrtCur <= sqrtL) return { amount0: amount0ForLiquidity(sqrtL, sqrtU, L, roundUp), amount1: 0n };
  if (sqrtCur >= sqrtU) return { amount0: 0n, amount1: amount1ForLiquidity(sqrtL, sqrtU, L, roundUp) };
  return {
    amount0: amount0ForLiquidity(sqrtCur, sqrtU, L, roundUp),
    amount1: amount1ForLiquidity(sqrtL, sqrtCur, L, roundUp),
  };
}

// ликвидность, которую можно получить из заданных количеств (как LiquidityAmounts.getLiquidityForAmounts)
export function liquidityForAmounts(sqrtCur, sqrtL, sqrtU, amount0, amount1) {
  const l0 = (a, b, x) => (x * ((a * b) / Q96)) / (b - a);
  const l1 = (a, b, y) => (y * Q96) / (b - a);
  if (sqrtCur <= sqrtL) return l0(sqrtL, sqrtU, amount0);
  if (sqrtCur >= sqrtU) return l1(sqrtL, sqrtU, amount1);
  const a = l0(sqrtCur, sqrtU, amount0);
  const b = l1(sqrtL, sqrtCur, amount1);
  return a < b ? a : b;
}
