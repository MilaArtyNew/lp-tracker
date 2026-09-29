// USD-цены токенов с DexScreener (самая ликвидная пара, где токен — base). Кэш на время жизни popup.
import chains from "../chains.json" with { type: "json" };
import { ZERO } from "./denom.js";

const cache = new Map();

async function fetchPrice(chain, addr) {
  const c = chains[chain];
  if (!c?.ds) return null;
  const r = await fetch(`https://api.dexscreener.com/tokens/v1/${c.ds}/${addr}`);
  if (!r.ok) return null;
  let best = null, liq = -1;
  for (const p of await r.json()) {
    const base = (p.baseToken?.address || "").toLowerCase(), quote = (p.quoteToken?.address || "").toLowerCase(), a = addr.toLowerCase();
    let price = null;
    if (base === a && p.priceUsd) price = Number(p.priceUsd);
    else if (quote === a && p.priceUsd && Number(p.priceNative) > 0) price = Number(p.priceUsd) / Number(p.priceNative);  // токен — котируемый в паре
    const l = Number(p.liquidity?.usd || 0);
    if (price && l > liq) { best = price; liq = l; }
  }
  return best;
}

export function pricesFor(chain) {
  const usdOf = (addr) => {
    const a = !addr || addr.toLowerCase() === ZERO ? chains[chain].weth : addr;   // нативный токен = его обёртка
    const key = `${chain}:${a.toLowerCase()}`;
    if (!cache.has(key)) cache.set(key, fetchPrice(chain, a).catch(() => null));
    return cache.get(key);
  };
  return { usdOf, ethUsd: () => usdOf(chains[chain].eth) };
}
