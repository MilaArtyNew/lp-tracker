import test from "node:test";
import assert from "node:assert/strict";
import { providersOf, withRpc } from "../src/core/chain.js";

const transport = () => Object.assign(new Error("missing revert data"), { code: "CALL_EXCEPTION", data: null });
const revert = () => Object.assign(new Error("execution reverted"), { code: "CALL_EXCEPTION", data: "0x08c379a0", reason: "nope" });
function fakes(fns) { const ps = providersOf("base"); const saved = ps.splice(0, ps.length, ...fns.map((f) => ({ f }))); return () => ps.splice(0, ps.length, ...saved); }

test("withRpc: сбой транспорта первого узла -> берём второй", async () => {
  const restore = fakes([() => { throw transport(); }, () => "ok-from-second"]);
  try { assert.equal(await withRpc("base", (p) => p.f()), "ok-from-second"); } finally { restore(); }
});

test("withRpc: настоящий revert с данными пробрасывается сразу, второй узел не трогаем", async () => {
  let calls = 0;
  const restore = fakes([() => { throw revert(); }, () => { calls++; return "no"; }]);
  try { await assert.rejects(withRpc("base", (p) => p.f()), /reverted/); assert.equal(calls, 0); } finally { restore(); }
});

test("withRpc: все узлы недоступны -> последняя ошибка", async () => {
  const restore = fakes([() => { throw new Error("a"); }, () => { throw new Error("b"); }]);
  try { await assert.rejects(withRpc("base", (p) => p.f()), /b/); } finally { restore(); }
});
