import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { AbiCoder, ZeroAddress, Interface } from "ethers";
import { sqrtRatioAtTick, Q96, MIN_TICK, MAX_TICK, floorTick } from "../src/core/tickmath.js";
import { amountsForLiquidity, liquidityForAmounts } from "../src/core/liquidity.js";
import { buildGrid } from "../src/core/grid.js";
import { buildMintGrid, buildCollect, buildClose, Actions, POSM_IFACE, poolIdOf, decodePositionTicks } from "../src/core/v4.js";
import chains from "../src/chains.json" with { type: "json" };

const coder = AbiCoder.defaultAbiCoder();

test("TickMath: известные значения", () => {
  assert.equal(sqrtRatioAtTick(0), Q96);
  assert.equal(sqrtRatioAtTick(MIN_TICK), 4295128739n);
  assert.equal(sqrtRatioAtTick(MAX_TICK), 1461446703485210103287273052203988822378723970342n);
});

test("TickMath: совпадает с float-оценкой и монотонен", () => {
  let prev = 0n;
  for (let t = -300000; t <= 300000; t += 7919) {
    const s = sqrtRatioAtTick(t);
    assert.ok(s > prev); prev = s;
    const approx = Math.sqrt(Math.pow(1.0001, t)) * Number(Q96);
    assert.ok(Math.abs(Number(s) - approx) / approx < 1e-9, `tick ${t}`);
  }
  assert.throws(() => sqrtRatioAtTick(MAX_TICK + 1));
});

test("liquidity: туда-обратно не даёт больше исходных сумм", () => {
  const cur = sqrtRatioAtTick(-200003), lo = sqrtRatioAtTick(-200100), hi = sqrtRatioAtTick(-199900);
  const a0 = 5n * 10n ** 18n, a1 = 12000n * 10n ** 6n;
  const L = liquidityForAmounts(cur, lo, hi, a0, a1);
  const need = amountsForLiquidity(cur, lo, hi, L, true);
  assert.ok(need.amount0 <= a0 + 2n && need.amount1 <= a1 + 2n);
});

// пул ETH(18)/USDC(6)-подобный: token0=ETH, token1=USDC
const CUR = -200003, SP = 10;
const base = { curTick: CUR, sqrtCur: sqrtRatioAtTick(CUR) + 12345n, spacing: SP, dec0: 18, dec1: 6, ranges: 6, fromPct: 2, toPct: 20, weights: "ascending" };
const USDC = 10_000n * 10n ** 6n, ETH = 3n * 10n ** 18n;

test("grid below/single_sided: только token1, ниже цены, стык в стык, в пределах бюджета", () => {
  const g = buildGrid({ ...base, side: "below", mode: "single_sided", budget: USDC });
  assert.equal(g.secondToken, 0n);
  assert.equal(g.totals.amount0, 0n);
  assert.equal(g.ranges.length, 6);
  for (const r of g.ranges) { assert.ok(r.tickUpper <= floorTick(CUR, SP)); assert.ok(r.tickLower % SP === 0); assert.ok(r.tickLower < r.tickUpper); }
  for (let i = 1; i < g.ranges.length; i++) assert.equal(g.ranges[i].tickUpper, g.ranges[i - 1].tickLower);
  assert.ok(g.totals.amount1 <= USDC + 6n && g.totals.amount1 > (USDC * 999n) / 1000n, `spent ${g.totals.amount1}`);
  // ascending: дальние получают больше капитала
  assert.ok(g.ranges[5].amount1 > g.ranges[0].amount1);
});

test("grid above/single_sided: только token0, выше цены", () => {
  const g = buildGrid({ ...base, side: "above", mode: "single_sided", budget: ETH, weights: "equal" });
  assert.equal(g.totals.amount1, 0n);
  for (const r of g.ranges) assert.ok(r.tickLower > CUR);
  for (let i = 1; i < g.ranges.length; i++) assert.equal(g.ranges[i].tickLower, g.ranges[i - 1].tickUpper);
  assert.ok(g.totals.amount0 <= ETH + 6n && g.totals.amount0 > (ETH * 999n) / 1000n);
  const a = g.ranges.map((r) => r.amount0);
  assert.ok(Math.max(...a.map(Number)) / Math.min(...a.map(Number)) < 1.01, "equal-веса дают равные суммы");
});

test("режимы: у цены standard требует второй токен, minimize — меньше, single — ноль", () => {
  for (const side of ["below", "above"]) {
    const p = { ...base, side, fromPct: 0, toPct: 10, ranges: 4, budget: side === "below" ? USDC : ETH, weights: "equal" };
    const st = buildGrid({ ...p, mode: "standard" });
    const mn = buildGrid({ ...p, mode: "minimize_second" });
    const ss = buildGrid({ ...p, mode: "single_sided" });
    assert.equal(ss.secondToken, 0n, `${side} single`);
    assert.ok(mn.secondToken <= st.secondToken, `${side} minimize<=standard`);
    // minimize не выходит за цену больше чем на один spacing
    const r0 = mn.ranges[0];
    if (side === "below") assert.ok(r0.tickUpper <= floorTick(CUR, SP) + SP); else assert.ok(r0.tickLower >= floorTick(CUR, SP));
  }
});

test("grid: полоса меньше N диапазонов уменьшает N с предупреждением; ошибки ввода", () => {
  const g = buildGrid({ ...base, side: "below", mode: "single_sided", fromPct: 5, toPct: 5.2, ranges: 20, budget: USDC });
  assert.ok(g.ranges.length < 20 && g.warnings.some((w) => w.includes("вмещает")));
  assert.throws(() => buildGrid({ ...base, side: "below", mode: "x", budget: USDC }));
  assert.throws(() => buildGrid({ ...base, side: "below", mode: "single_sided", fromPct: 10, toPct: 5, budget: USDC }));
  assert.throws(() => buildGrid({ ...base, side: "below", mode: "single_sided", budget: 0n }));
});

const KEY = { currency0: ZeroAddress, currency1: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", fee: 500, tickSpacing: 10, hooks: ZeroAddress };
const OWNER = "0x" + "ab".repeat(20), POSM = chains.base.posm;

function decodeModify(tx) {
  const [unlock, deadline] = POSM_IFACE.decodeFunctionData("modifyLiquidities", tx.data);
  const [actions, params] = coder.decode(["bytes", "bytes[]"], unlock);
  return { actions: [...Buffer.from(actions.slice(2), "hex")], params, deadline };
}

test("calldata mint: actions и параметры расшифровываются обратно, native → value + SWEEP", () => {
  const g = buildGrid({ ...base, side: "above", mode: "single_sided", budget: ETH });
  const tx = buildMintGrid({ posm: POSM, poolKey: KEY, ranges: g.ranges, recipient: OWNER, deadline: 999n });
  const d = decodeModify(tx);
  assert.deepEqual(d.actions, [...g.ranges.map(() => Actions.MINT_POSITION), Actions.SETTLE_PAIR, Actions.SWEEP]);
  assert.equal(tx.value, g.totals.amount0Max);
  assert.equal(tx.to.toLowerCase(), POSM.toLowerCase());
  const m0 = coder.decode(["(address,address,uint24,int24,address)", "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"], d.params[0]);
  assert.equal(Number(m0[1]), g.ranges[0].tickLower);
  assert.equal(m0[3], g.ranges[0].liquidity);
  assert.equal(m0[6].toLowerCase(), OWNER); // NFT сразу на основной кошелёк
});

test("calldata mint: два ERC20 → value=0, без SWEEP; native как currency1 запрещён", () => {
  const key = { ...KEY, currency0: "0x4200000000000000000000000000000000000006" };
  const g = buildGrid({ ...base, side: "above", mode: "single_sided", budget: ETH });
  const tx = buildMintGrid({ posm: POSM, poolKey: key, ranges: g.ranges, recipient: OWNER, deadline: 1n });
  assert.equal(tx.value, 0n);
  assert.equal(decodeModify(tx).actions.at(-1), Actions.SETTLE_PAIR);
  assert.throws(() => buildMintGrid({ posm: POSM, poolKey: { ...KEY, currency0: key.currency1, currency1: ZeroAddress }, ranges: g.ranges, recipient: OWNER, deadline: 1n }));
});

test("calldata close/collect", () => {
  const c = decodeModify(buildClose({ posm: POSM, tokenId: 77n, currency0: KEY.currency0, currency1: KEY.currency1, recipient: OWNER, deadline: 5n }));
  assert.deepEqual(c.actions, [Actions.BURN_POSITION, Actions.TAKE_PAIR]);
  const take = coder.decode(["address", "address", "address"], c.params[1]);
  assert.equal(take[2].toLowerCase(), OWNER);                       // деньги — только на основной кошелёк
  const f = decodeModify(buildCollect({ posm: POSM, tokenId: 77n, currency0: KEY.currency0, currency1: KEY.currency1, recipient: OWNER, deadline: 5n }));
  assert.deepEqual(f.actions, [Actions.DECREASE_LIQUIDITY, Actions.TAKE_PAIR]);
  assert.equal(coder.decode(["uint256", "uint256", "uint128", "uint128", "bytes"], f.params[0])[1], 0n); // liquidity = 0
});

test("decodePositionTicks: упаковка [poolId|upper|lower|flag]", () => {
  const pack = (lo, up) => (BigInt(up & 0xffffff) << 32n) | (BigInt(lo & 0xffffff) << 8n) | (0xabcdn << 56n);
  assert.deepEqual(decodePositionTicks(pack(-887270, -100)), { tickLower: -887270, tickUpper: -100 });
  assert.deepEqual(decodePositionTicks(pack(60, 120)), { tickLower: 60, tickUpper: 120 });
});

test("poolId совпадает с известным значением ETH/USDC v4 (Python compute_pool_id)", () => {
  assert.match(poolIdOf(KEY), /^0x[0-9a-f]{64}$/);
});

test("chains.json совпадает с адресами в Python-боте (нет расхождения)", () => {
  const py = readFileSync(new URL("../../modules/rpc_tracker.py", import.meta.url), "utf8").toLowerCase();
  for (const [name, c] of Object.entries(chains)) {
    assert.ok(py.includes(c.posm.toLowerCase()), `${name} posm`);
    assert.ok(py.includes(c.stateView.toLowerCase()), `${name} stateView`);
  }
});
