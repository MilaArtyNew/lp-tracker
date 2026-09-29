import asyncio
import logging
import aiohttp
from config import COINGECKO_API

log = logging.getLogger(__name__)

PLATFORMS = {
    "ethereum":           "ethereum",
    "arbitrum":           "arbitrum-one",
    "base":               "base",
    "bnb":                "binance-smart-chain",
}

_HEADERS = {"User-Agent": "lp-tracker-bot/1.0"}

ZERO_ADDR = "0x0000000000000000000000000000000000000000"
# Сети, которых нет в CoinGecko по контракту: цены берём с DexScreener (slug сети).
DEXSCREENER_ONLY = {"robinhood": "robinhood"}
# Нативный токен: по нулевому адресу цены нет ни у CoinGecko, ни у DexScreener -> берём цену его обёртки
WRAPPED_NATIVE = {
    "ethereum":  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "base":      "0x4200000000000000000000000000000000000006",
    "arbitrum":  "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "bnb":       "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "robinhood": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
}


async def _get(session: aiohttp.ClientSession, url: str, params: dict) -> dict | None:
    for attempt in range(3):
        try:
            async with session.get(
                url, params=params,
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status == 429:
                    retry_after = int(r.headers.get("Retry-After", 10))
                    log.warning("CoinGecko 429, wait %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if r.status != 200:
                    log.warning("CoinGecko %s → HTTP %s", url, r.status)
                    return None
                return await r.json()
        except Exception as e:
            log.warning("CoinGecko request error (attempt %s): %s", attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    return None


def _pick_price(pairs: list, address: str) -> float | None:
    """USD-цена токена по самой ликвидной паре: токен может быть базовым или котируемым."""
    best, best_liq = None, -1.0
    for p in pairs or []:
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        base = (p.get("baseToken") or {}).get("address", "").lower()
        quote = (p.get("quoteToken") or {}).get("address", "").lower()
        price = None
        if base == address.lower() and p.get("priceUsd"):
            price = float(p["priceUsd"])
        elif quote == address.lower() and p.get("priceUsd") and p.get("priceNative"):
            # priceUsd — цена базового токена; priceNative — его цена в котируемом => 1 котируемый = usd/native
            native = float(p["priceNative"])
            price = float(p["priceUsd"]) / native if native > 0 else None
        if price and liq > best_liq:
            best, best_liq = price, liq
    return best


async def _price_dexscreener(address: str, chain: str) -> float | None:
    """USD-цена токена по самой ликвидной паре DexScreener. Нативный токен -> цена WETH сети."""
    slug = DEXSCREENER_ONLY[chain]
    url = f"https://api.dexscreener.com/tokens/v1/{slug}/{address}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status != 200:
                    log.warning("DexScreener %s -> HTTP %s", url, r.status)
                    return None
                pairs = await r.json()
    except Exception as e:
        log.warning("DexScreener error for %s: %s", address, e)
        return None
    return _pick_price(pairs, address)


async def _native_price(chain: str) -> float | None:
    wrapped = WRAPPED_NATIVE.get(chain)
    return await get_price_coingecko(wrapped, chain) if wrapped else None


async def get_price_coingecko(token: str, chain: str = None) -> float | None:
    if token.startswith("0x"):
        if token.lower() == ZERO_ADDR and chain:
            return await _native_price(chain)
        if chain in DEXSCREENER_ONLY:
            return await _price_dexscreener(token, chain)
        return await _price_by_contract(token, chain)
    else:
        return await _price_by_ticker(token)


async def _price_by_contract(address: str, chain: str = None) -> float | None:
    address = address.lower()
    platforms = [PLATFORMS[chain]] if chain and chain in PLATFORMS else list(PLATFORMS.values())

    async def try_platform(platform: str) -> float | None:
        url = f"{COINGECKO_API}/simple/token_price/{platform}"
        params = {"contract_addresses": address, "vs_currencies": "usd"}
        async with aiohttp.ClientSession() as s:
            data = await _get(s, url, params)
        if not data:
            return None
        price = data.get(address, {}).get("usd")
        return float(price) if price else None

    results = await asyncio.gather(*[try_platform(p) for p in platforms])
    return next((r for r in results if r is not None), None)


async def _price_by_ticker(ticker: str) -> float | None:
    async with aiohttp.ClientSession() as s:
        data = await _get(s, f"{COINGECKO_API}/search", {"query": ticker})
        if not data:
            log.warning("CoinGecko search failed for %s", ticker)
            return None

        coins = data.get("coins", [])
        if not coins:
            log.warning("CoinGecko: no coins found for %s", ticker)
            return None

        ticker_upper = ticker.upper()
        coin_id = next(
            (c["id"] for c in coins if c.get("symbol", "").upper() == ticker_upper),
            coins[0]["id"],
        )
        log.info("CoinGecko: %s → id=%s", ticker, coin_id)

        data2 = await _get(s, f"{COINGECKO_API}/simple/price",
                           {"ids": coin_id, "vs_currencies": "usd"})
        if not data2:
            return None
        price = data2.get(coin_id, {}).get("usd")
        return float(price) if price else None
