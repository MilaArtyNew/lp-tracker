import test from "node:test";
import assert from "node:assert/strict";
import { assetView, convertRange, moneyKind } from "../src/core/denom.js";

const prices = (m) => ({ usdOf: async (a) => m[a.toLowerCase()] ?? null, ethUsd: async () => m.eth ?? null });
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} != ${b}`);

test("moneyKind: стейблы, ETH, прочее", () => {
  assert.equal(moneyKind("USDT"), "usd"); assert.equal(moneyKind("usdg"), "usd");
  assert.equal(moneyKind("WETH"), "eth"); assert.equal(moneyKind("PEPE"), null);
});

test("ETH/USDT: цена актива = цена пула, в USD и в ETH", async () => {
  const p = { symbol0: "ETH", symbol1: "USDT", token0: "0x0", token1: "0xu", price_lower: 2000, price_upper: 4000, current_price: 3000 };
  const v = assetView(p);
  assert.equal(v.inverted, false); assert.equal(v.asset, "ETH");
  const usd = await convertRange(v, "usd", prices({ eth: 3000 }));
  assert.deepEqual([usd.lo, usd.hi, usd.cur, usd.unit], [2000, 4000, 3000, "USD"]);
  const eth = await convertRange(v, "eth", prices({ eth: 3000 }));
  near(eth.lo, 2000 / 3000); near(eth.hi, 4000 / 3000); near(eth.cur, 1); assert.equal(eth.unit, "ETH");
});

test("USDC/TOKEN (token0 — стейбл): пара переворачивается, границы меняются местами", async () => {
  // 1 USDC = 100 TOKEN; диапазон 50..200 TOKEN за USDC -> цена TOKEN в USDC: 1/200..1/50
  const p = { symbol0: "USDC", symbol1: "TOKEN", token0: "0xu", token1: "0xt", price_lower: 50, price_upper: 200, current_price: 100 };
  const v = assetView(p);
  assert.equal(v.inverted, true); assert.equal(v.asset, "TOKEN"); assert.equal(v.quote, "USDC");
  near(v.lo, 1 / 200); near(v.hi, 1 / 50); near(v.cur, 0.01); assert.ok(v.lo < v.hi);
  const usd = await convertRange(v, "usd", prices({}));
  near(usd.cur, 0.01);
});

test("TOKEN/ETH: USD = цена в ETH × курс ETH; ETH-режим = как в пуле", async () => {
  const p = { symbol0: "MEME", symbol1: "WETH", token0: "0xm", token1: "0xw", price_lower: 0.0001, price_upper: 0.0003, current_price: 0.0002 };
  const v = assetView(p);
  const usd = await convertRange(v, "usd", prices({ eth: 2700 }));
  near(usd.lo, 0.27); near(usd.hi, 0.81); near(usd.cur, 0.54);
  const eth = await convertRange(v, "eth", prices({ eth: 2700 }));
  near(eth.lo, 0.0001); near(eth.hi, 0.0003);
});

test("TOKEN/OTHER: берём USD-цену второго токена; нет курса -> явная ошибка, не «0»", async () => {
  const p = { symbol0: "A", symbol1: "B", token0: "0xa", token1: "0xb", price_lower: 1, price_upper: 2, current_price: 1.5 };
  const v = assetView(p);
  const ok = await convertRange(v, "usd", prices({ "0xb": 0.5 }));
  near(ok.lo, 0.5); near(ok.hi, 1);
  const bad = await convertRange(v, "usd", prices({}));
  assert.ok(bad.error && bad.lo === undefined);
});

test("режим pool не требует курсов", async () => {
  const p = { symbol0: "A", symbol1: "B", token0: "0xa", token1: "0xb", price_lower: 1, price_upper: 2, current_price: 1.5 };
  const r = await convertRange(assetView(p), "pool", prices({}));
  assert.deepEqual([r.lo, r.hi, r.unit], [1, 2, "B"]);
});

test("режим mc: границы в капитализации = USD-цена × предложение; нет данных -> явная ошибка", async () => {
  const p = { symbol0: "MEME", symbol1: "USDT", token0: "0xm", token1: "0xu", price_lower: 0.5, price_upper: 2, current_price: 1 };
  const pr = { ...prices({}), supplyOf: async (a) => (a === "0xm" ? 1_000_000 : null) };
  const r = await convertRange(assetView(p), "mc", pr);
  assert.deepEqual([r.lo, r.hi, r.cur, r.unit], [500_000, 2_000_000, 1_000_000, "MC"]);
  const bad = await convertRange(assetView(p), "mc", { ...prices({}), supplyOf: async () => null });
  assert.ok(bad.error);
  // токен/ETH: капитализация по USD-цене актива (ETH-цена × цена в ETH) × предложение
  const q = { symbol0: "MEME", symbol1: "WETH", token0: "0xm", token1: "0xw", price_lower: 0.0001, price_upper: 0.0002, current_price: 0.00015 };
  const e = await convertRange(assetView(q), "mc", { ...prices({ eth: 2000 }), supplyOf: async () => 10_000_000 });
  near(e.cur, 0.00015 * 2000 * 10_000_000);
});
