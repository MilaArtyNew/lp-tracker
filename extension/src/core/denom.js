// В какой единице показывать рендж: цена пула, USD (стейблы USDT/USDC/USDG…) или ETH.
// Цена пула всегда token1/token0. Показываем цену АКТИВА: если token0 — «денежный» токен (стейбл/ETH),
// а token1 — нет, пара переворачивается (1/цена, границы меняются местами).
const STABLES = new Set(["USDT", "USDC", "USDG", "USD1", "USDS", "DAI", "FDUSD", "PYUSD", "USDE", "USDC.E", "USDT0", "BUSD", "TUSD"]);
const ETHLIKE = new Set(["ETH", "WETH"]);
const ZERO = "0x0000000000000000000000000000000000000000";

export const moneyKind = (sym) => {
  const s = String(sym || "").toUpperCase();
  return STABLES.has(s) ? "usd" : ETHLIKE.has(s) ? "eth" : null;
};

// -> { asset, quote, quoteAddr, lo, hi, cur, inverted }, цены — quote за 1 asset
export function assetView(p) {
  const lo = Number(p.price_lower), hi = Number(p.price_upper), cur = Number(p.current_price);
  const flip = moneyKind(p.symbol0) && !moneyKind(p.symbol1);
  if (!flip) return { asset: p.symbol0, quote: p.symbol1, quoteAddr: p.token1, assetAddr: p.token0, lo, hi, cur, inverted: false };
  return { asset: p.symbol1, quote: p.symbol0, quoteAddr: p.token0, assetAddr: p.token1, lo: hi > 0 ? 1 / hi : 0, hi: lo > 0 ? 1 / lo : Infinity, cur: cur > 0 ? 1 / cur : 0, inverted: true };
}

// prices: { usdOf(addr) -> Promise<number|null>, ethUsd() -> Promise<number|null> }
// Возвращает { lo, hi, cur, unit } или { error } если не хватает курса.
export async function convertRange(view, mode, prices) {
  if (mode === "pool") return { lo: view.lo, hi: view.hi, cur: view.cur, unit: view.quote };
  const kind = moneyKind(view.quote);
  let usdPerQuote = kind === "usd" ? 1 : kind === "eth" ? await prices.ethUsd() : await prices.usdOf(view.quoteAddr);
  if (!(usdPerQuote > 0)) return { error: `нет USD-цены для ${view.quote}` };
  let k = usdPerQuote;
  let unit = "USD";
  if (mode === "mc") {     // капитализация = цена актива в USD × предложение (marketCap / priceUsd из DexScreener)
    const supply = await prices.supplyOf(view.assetAddr);
    if (!(supply > 0)) return { error: `нет капитализации для ${view.asset}` };
    k = usdPerQuote * supply; unit = "MC";
    return { lo: view.lo * k, hi: view.hi * k, cur: view.cur * k, unit };
  }
  if (mode === "eth") {
    const eth = await prices.ethUsd();
    if (!(eth > 0)) return { error: "нет USD-цены ETH" };
    k = usdPerQuote / eth; unit = "ETH";
  }
  return { lo: view.lo * k, hi: view.hi * k, cur: view.cur * k, unit };
}

export { ZERO };
