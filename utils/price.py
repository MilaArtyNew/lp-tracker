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


async def get_price_coingecko(token: str, chain: str = None) -> float | None:
    if token.startswith("0x"):
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
