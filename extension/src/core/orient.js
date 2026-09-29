// Ориентация Grid «актив / котировка». В Uniswap порядок токенов — по адресу, а не по смыслу: в v4 нативный ETH
// всегда currency0, поэтому в пуле токен/ETH актив = currency1, а цена пула (token1/token0) обратна цене актива.
// Пользователь мыслит ценой актива в котировке (ETH или USDT за токен), поэтому Grid задаётся в ней,
// а здесь переводится в терминах пула для buildGrid().
import { moneyKind } from "./denom.js";

const isMoney = (sym) => !!moneyKind(sym);

// Актив — не «денежный» токен пары; если оба или ни один — токен0 (как раньше).
export function orient(sym0, sym1) {
  const flipped = isMoney(sym0) && !isMoney(sym1);
  return flipped
    ? { flipped: true, asset: "token1", quote: "token0", assetSym: sym1, quoteSym: sym0 }
    : { flipped: false, asset: "token0", quote: "token1", assetSym: sym0, quoteSym: sym1 };
}

// bids/asks в терминах актива -> сторона и проценты для buildGrid (в терминах пула)
export function toPoolParams({ flipped }, kind /* "bids" | "asks" */, fromPct, toPct) {
  if (!flipped) return { side: kind === "bids" ? "below" : "above", fromPct, toPct };
  // Цена пула = 1 / цена актива. Bid: цена актива на X% НИЖЕ => цена пула на (1/(1-X)-1) ВЫШЕ; ask — зеркально.
  const up = (x) => (1 / (1 - x / 100) - 1) * 100;      // актив -x%  -> пул +?%
  const down = (x) => (1 - 1 / (1 + x / 100)) * 100;    // актив +x%  -> пул -?%
  if (kind === "bids") {
    if (toPct >= 100) throw new Error("для bids To < 100%");
    return { side: "above", fromPct: up(fromPct), toPct: up(toPct) };
  }
  return { side: "below", fromPct: down(fromPct), toPct: down(toPct) };
}

// Диапазон из buildGrid (цена пула) -> цена актива в котировке и % от текущей цены актива (знаковый)
export function toAssetTerms({ flipped }, r, curPoolPrice) {
  const lo = flipped ? 1 / r.priceUpper : r.priceLower, hi = flipped ? 1 / r.priceLower : r.priceUpper;
  const cur = flipped ? 1 / curPoolPrice : curPoolPrice;
  return { lo, hi, lowPct: (lo / cur - 1) * 100, highPct: (hi / cur - 1) * 100 };
}
