import test from "node:test";
import assert from "node:assert/strict";
import { orient, toPoolParams, toAssetTerms } from "../src/core/orient.js";
import { buildGrid } from "../src/core/grid.js";
import { sqrtRatioAtTick, tickToPrice } from "../src/core/tickmath.js";

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} != ${b}`);

test("orient: ETH(токен0)/TOKEN -> актив = токен1; TOKEN/USDT -> актив = токен0; USDT/TOKEN -> актив = токен1", () => {
  assert.deepEqual([orient("ETH", "PEPE").flipped, orient("ETH", "PEPE").assetSym], [true, "PEPE"]);
  assert.deepEqual([orient("PEPE", "USDT").flipped, orient("PEPE", "USDT").quoteSym], [false, "USDT"]);
  assert.deepEqual([orient("USDG", "ROBIN").flipped, orient("USDG", "ROBIN").asset], [true, "token1"]);
  assert.equal(orient("A", "B").flipped, false);                       // ни один не «денежный» — как раньше
});

test("toPoolParams: без переворота — как есть; с переворотом bid -> сторона above, проценты пересчитаны", () => {
  assert.deepEqual(toPoolParams({ flipped: false }, "bids", 2, 20), { side: "below", fromPct: 2, toPct: 20 });
  assert.deepEqual(toPoolParams({ flipped: false }, "asks", 2, 20), { side: "above", fromPct: 2, toPct: 20 });
  const b = toPoolParams({ flipped: true }, "bids", 20, 50);            // актив -20% ⇒ цена пула ×1.25 (+25%), -50% ⇒ ×2 (+100%)
  assert.equal(b.side, "above"); near(b.fromPct, 25); near(b.toPct, 100);
  const a = toPoolParams({ flipped: true }, "asks", 25, 100);           // актив +25% ⇒ пул ×0.8 (-20%), +100% ⇒ ×0.5 (-50%)
  assert.equal(a.side, "below"); near(a.fromPct, 20); near(a.toPct, 50);
  assert.throws(() => toPoolParams({ flipped: true }, "bids", 10, 100));
});

// пул ETH(c0,18)/TOKEN(c1,18): цена пула = токенов за ETH
const CUR = 76000, SP = 10, D = 18;
const base = { curTick: CUR, sqrtCur: sqrtRatioAtTick(CUR) + 777n, spacing: SP, dec0: D, dec1: D, ranges: 4, mode: "single_sided", weights: "equal" };
const curPool = tickToPrice(CUR, D, D);

test("flipped-пул ETH/TOKEN: bids в терминах актива тратят только ETH (token0), диапазоны ниже цены актива", () => {
  const o = orient("ETH", "TOKEN");
  const p = toPoolParams(o, "bids", 5, 40);
  const g = buildGrid({ ...base, ...p, budget: 5n * 10n ** 18n });
  assert.equal(g.totals.amount1, 0n); assert.ok(g.totals.amount0 > 0n);   // ETH — токен0
  for (const r of g.ranges) {
    const t = toAssetTerms(o, r, curPool);
    assert.ok(t.lo < t.hi); assert.ok(t.highPct < 0 && t.lowPct < 0, "оба края ниже цены актива");
    assert.ok(t.lowPct > -41 && t.highPct <= -4.9 + 0.5, `край в пределах полосы 5..40%: ${t.lowPct}..${t.highPct}`);
  }
});

test("flipped-пул ETH/TOKEN: asks тратят только TOKEN (token1), диапазоны выше цены актива", () => {
  const o = orient("ETH", "TOKEN");
  const p = toPoolParams(o, "asks", 5, 40);
  const g = buildGrid({ ...base, ...p, budget: 1000n * 10n ** 18n });
  assert.equal(g.totals.amount0, 0n); assert.ok(g.totals.amount1 > 0n);
  for (const r of g.ranges) { const t = toAssetTerms(o, r, curPool); assert.ok(t.lowPct > 0 && t.highPct > 0); }
});

test("не-flipped пул TOKEN/USDT: bids = below, депозит в USDT (token1); проценты в терминах актива совпадают с пулом", () => {
  const o = orient("TOKEN", "USDT");
  const g = buildGrid({ ...base, dec1: 6, ...toPoolParams(o, "bids", 3, 30), budget: 1000n * 10n ** 6n });
  assert.equal(g.totals.amount0, 0n);
  const t = toAssetTerms(o, g.ranges[0], tickToPrice(CUR, D, 6));
  assert.ok(t.highPct < 0 && t.highPct > -6);
});

test("оба ориентации дают эквивалентную лесенку по цене актива (границы полосы совпадают до тика)", () => {
  // Один экономический bid -5..-40% в двух зеркальных пулах: границы в терминах актива должны совпасть
  const flip = orient("ETH", "TOKEN"), plain = orient("TOKEN", "ETH");
  const gf = buildGrid({ ...base, ...toPoolParams(flip, "bids", 5, 40), budget: 10n ** 18n });
  const mirrorTick = -CUR;
  const gp = buildGrid({ ...base, curTick: mirrorTick, sqrtCur: sqrtRatioAtTick(mirrorTick) + 5n, ...toPoolParams(plain, "bids", 5, 40), budget: 10n ** 18n });
  const tf = toAssetTerms(flip, gf.ranges.at(-1), curPool), tp = toAssetTerms(plain, gp.ranges.at(-1), tickToPrice(mirrorTick, D, D));
  near(tf.lowPct, tp.lowPct, 5e-3);                   // самая дальняя граница ≈ -40% в обоих
  assert.ok(Math.abs(tf.lowPct + 40) < 1.0 && Math.abs(tp.lowPct + 40) < 1.0);
});
