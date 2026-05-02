import asyncio
import aiohttp
from config import COINGECKO_API

PLATFORMS = {
    "ethereum":           "ethereum",
    "arbitrum":           "arbitrum-one",
    "base":               "base",
    "bnb":                "binance-smart-chain",
}


async def get_price_coingecko(token: str, chain: str = None) -> float | None:
    if token.startswith("0x"):
        return await _price_by_contract(token, chain)
    else:
        return await _price_by_ticker(token)


async def _price_by_contract(address: str, chain: str = None) -> float | None:
    address = address.lower()

    # Если сеть известна — пробуем сразу её, иначе все параллельно
    platforms = [PLATFORMS[chain]] if chain and chain in PLATFORMS else list(PLATFORMS.values())

    async def try_platform(platform: str) -> float | None:
        url = f"{COINGECKO_API}/simple/token_price/{platform}"
        params = {"contract_addresses": address, "vs_currencies": "usd"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    data = await r.json()
                    price = data.get(address, {}).get("usd")
                    return float(price) if price else None
        except Exception:
            return None

    results = await asyncio.gather(*[try_platform(p) for p in platforms])
    return next((r for r in results if r is not None), None)


async def _price_by_ticker(ticker: str) -> float | None:
    search_url = f"{COINGECKO_API}/search"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                search_url,
                params={"query": ticker},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()

            coins = data.get("coins", [])
            if not coins:
                return None

            ticker_upper = ticker.upper()
            coin_id = next(
                (c["id"] for c in coins if c.get("symbol", "").upper() == ticker_upper),
                coins[0]["id"],
            )

        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{COINGECKO_API}/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                price = data.get(coin_id, {}).get("usd")
                return float(price) if price else None

    except Exception:
        return None
