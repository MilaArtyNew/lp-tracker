// Чтение состояния сети через RPC (ethers JsonRpcProvider). Запись — только через кошелёк (wallet.js).
import { JsonRpcProvider, FetchRequest, getAddress } from "ethers";
import chains from "../chains.json" with { type: "json" };
import { POSM_IFACE, STATE_VIEW_IFACE, ERC20_IFACE, PERMIT2, PERMIT2_IFACE, decodePositionTicks, poolIdOf, isNative } from "./v4.js";
import { sqrtRatioAtTick, floorTick } from "./tickmath.js";
import { amountsForLiquidity } from "./liquidity.js";

export const CHAINS = chains;
const providers = {};
// Все RPC сети (по порядку). Публичные узлы режут по лимиту — тогда идём к следующему.
export function providersOf(chain) {
  const c = chains[chain];
  if (!c) throw new Error(`сеть ${chain} не настроена`);
  return (providers[chain] ||= c.rpc.map((u) => {
    // быстрый отказ: при 429/зависании сразу идём к следующему узлу, а не ждём длинный backoff ethers
    const req = new FetchRequest(u);
    req.timeout = 8000;
    req.setThrottleParams({ maxAttempts: 2, slotInterval: 150 });
    return new JsonRpcProvider(req, c.chainId, { staticNetwork: true });
  }));
}
export const provider = (chain) => providersOf(chain)[0];

// Настоящий revert (есть данные ревёрта) не повторяем; всё остальное считаем сбоем транспорта.
const isRealRevert = (e) => e?.code === "CALL_EXCEPTION" && (e.data != null && e.data !== "0x" || e.reason);
export async function withRpc(chain, fn) {
  let last;
  for (const p of providersOf(chain)) {
    try { return await fn(p); }
    catch (e) { if (isRealRevert(e)) throw e; last = e; }
  }
  throw last;
}

const call = async (chain, to, iface, fn, args, from) => {
  const data = iface.encodeFunctionData(fn, args);
  const raw = await withRpc(chain, (p) => p.call({ to, data, from }));
  return iface.decodeFunctionResult(fn, raw);
};

export async function getSlot0(chain, poolId) {
  const [sqrtPriceX96, tick, , lpFee] = await call(chain, chains[chain].stateView, STATE_VIEW_IFACE, "getSlot0", [poolId]);
  return { sqrtPriceX96, tick: Number(tick), lpFee: Number(lpFee) };
}

// PoolKey по poolId (bytes32): PositionManager хранит ключи по первым 25 байтам
export async function getPoolKey(chain, poolId) {
  const id25 = "0x" + poolId.slice(2, 52);
  const [currency0, currency1, fee, tickSpacing, hooks] = await call(chain, chains[chain].posm, POSM_IFACE, "poolKeys", [id25]);
  if (isNative(currency0) && isNative(currency1)) throw new Error("пул не найден в PositionManager этой сети");
  return { currency0, currency1, fee: Number(fee), tickSpacing: Number(tickSpacing), hooks };
}

export async function tokenInfo(chain, addr) {
  if (isNative(addr)) return { address: addr, symbol: chains[chain].native, decimals: 18, native: true };
  const [symbol] = await call(chain, addr, ERC20_IFACE, "symbol", []).catch(() => ["?"]);
  const [decimals] = await call(chain, addr, ERC20_IFACE, "decimals", []);
  return { address: getAddress(addr), symbol, decimals: Number(decimals), native: false };
}

export async function balanceOf(chain, token, owner) {
  if (isNative(token)) return withRpc(chain, (p) => p.getBalance(owner));
  const [b] = await call(chain, token, ERC20_IFACE, "balanceOf", [owner]);
  return b;
}

export async function poolState(chain, poolId) {
  const poolKey = await getPoolKey(chain, poolId);
  const [slot0, t0, t1] = await Promise.all([getSlot0(chain, poolId), tokenInfo(chain, poolKey.currency0), tokenInfo(chain, poolKey.currency1)]);
  if (slot0.sqrtPriceX96 === 0n) throw new Error("пул не инициализирован");
  return { poolKey, poolId, ...slot0, token0: t0, token1: t1 };
}

// Позиция для закрытия: ключ пула, тики, ликвидность и ожидаемые суммы (для min при слиппедже)
export async function positionState(chain, tokenId, from) {
  const posm = chains[chain].posm;
  const [[pk, info], [liq], [owner]] = await Promise.all([
    call(chain, posm, POSM_IFACE, "getPoolAndPositionInfo", [tokenId]),
    call(chain, posm, POSM_IFACE, "getPositionLiquidity", [tokenId]),
    call(chain, posm, POSM_IFACE, "ownerOf", [tokenId]),
  ]);
  const poolKey = { currency0: pk[0], currency1: pk[1], fee: Number(pk[2]), tickSpacing: Number(pk[3]), hooks: pk[4] };
  const { tickLower, tickUpper } = decodePositionTicks(info);
  const slot0 = await getSlot0(chain, poolIdOf(poolKey));
  const expected = amountsForLiquidity(slot0.sqrtPriceX96, sqrtRatioAtTick(tickLower), sqrtRatioAtTick(tickUpper), liq, false);
  return { poolKey, tickLower, tickUpper, liquidity: liq, owner, slot0, expected };
}

export async function permit2Status(chain, owner, token, spender) {
  const [erc20] = await call(chain, token, ERC20_IFACE, "allowance", [owner, PERMIT2]);
  const [amount, expiration] = await call(chain, PERMIT2, PERMIT2_IFACE, "allowance", [owner, token, spender]);
  return { erc20: erc20, permit2Amount: amount, permit2Expiration: Number(expiration) };
}

export async function simulate(chain, tx, from) {
  try {
    const req = { to: tx.to, data: tx.data, value: tx.value, from };
    await withRpc(chain, (p) => p.call(req));
    const gas = await withRpc(chain, (p) => p.estimateGas(req));
    return { ok: true, gas };
  } catch (e) {
    return { ok: false, error: e.shortMessage || e.message };
  }
}
