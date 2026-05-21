import asyncio
import aiohttp
from dataclasses import dataclass
from modules.uniswap_math import get_amounts, sqrt_price_x96_to_price, position_status
from utils.price import get_price_coingecko

SUBGRAPHS = {
    "ethereum": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
    "arbitrum": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-arbitrum",
    "base":     "https://api.studio.thegraph.com/query/48211/uniswap-v3-base/0.0.2",
    "bnb":      "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-bsc",
}

GQL_QUERY = """
{
  positions(
    where: { owner: "%s", liquidity_gt: "0" }
    first: 50
    orderBy: id
    orderDirection: desc
  ) {
    id
    pool {
      id
      sqrtPrice
      tick
      feeTier
      token0 { id symbol decimals }
      token1 { id symbol decimals }
    }
    tickLower { tickIdx }
    tickUpper { tickIdx }
    liquidity
    depositedToken0
    depositedToken1
    collectedFeesToken0
    collectedFeesToken1
  }
}
"""


@dataclass
class LPPosition:
    token_id: str
    pair: str
    token0_symbol: str
    token1_symbol: str
    fee_tier: str
    price_lower: float
    price_upper: float
    current_price: float
    status: str
    amount0: float
    amount1: float
    value_usd: float
    deposited0: float
    deposited1: float
    fees0: float
    fees1: float
    fees_usd: float
    _deposited_usd: float = 0.0
    token0_address: str = ""
    token1_address: str = ""


async def fetch_all_chains(wallet: str) -> list[LPPosition]:
    tasks = [fetch_positions(wallet, chain) for chain in SUBGRAPHS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    positions = []
    for r in results:
        if isinstance(r, list):
            positions.extend(r)
    return positions


async def fetch_positions(wallet: str, chain: str) -> list[LPPosition]:
    url = SUBGRAPHS.get(chain)
    if not url:
        raise ValueError(f"Unsupported chain: {chain}")

    query = GQL_QUERY % wallet.lower()

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={"query": query},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()

    raw_positions = data.get("data", {}).get("positions", [])
    if not raw_positions:
        return []

    results = []
    for pos in raw_positions:
        try:
            result = await _process_position(pos, chain)
            if result:
                results.append(result)
        except Exception:
            continue

    return results


async def _process_position(pos: dict, chain: str) -> LPPosition | None:
    pool = pos["pool"]
    t0 = pool["token0"]
    t1 = pool["token1"]

    decimals0 = int(t0["decimals"])
    decimals1 = int(t1["decimals"])
    liquidity = int(pos["liquidity"])
    sqrt_price = int(pool["sqrtPrice"])
    current_tick = int(pool["tick"])
    tick_lower = int(pos["tickLower"]["tickIdx"])
    tick_upper = int(pos["tickUpper"]["tickIdx"])
    fee_tier = int(pool["feeTier"]) / 10000

    current_price = sqrt_price_x96_to_price(sqrt_price, decimals0, decimals1)
    price_lower = sqrt_price_x96_to_price(
        int((1.0001 ** (tick_lower / 2)) * (2 ** 96)), decimals0, decimals1
    )
    price_upper = sqrt_price_x96_to_price(
        int((1.0001 ** (tick_upper / 2)) * (2 ** 96)), decimals0, decimals1
    )

    raw_amt0, raw_amt1 = get_amounts(liquidity, sqrt_price, tick_lower, tick_upper, current_tick)
    amount0 = raw_amt0 / (10 ** decimals0)
    amount1 = raw_amt1 / (10 ** decimals1)

    deposited0 = float(pos.get("depositedToken0", 0))
    deposited1 = float(pos.get("depositedToken1", 0))
    fees0 = float(pos.get("collectedFeesToken0", 0))
    fees1 = float(pos.get("collectedFeesToken1", 0))

    price0_usd = await get_price_coingecko(t0["id"], chain) or 0.0
    price1_usd = await get_price_coingecko(t1["id"], chain) or 0.0

    value_usd = amount0 * price0_usd + amount1 * price1_usd
    fees_usd = fees0 * price0_usd + fees1 * price1_usd
    deposited_usd = deposited0 * price0_usd + deposited1 * price1_usd

    status = position_status(current_tick, tick_lower, tick_upper)

    return LPPosition(
        token_id=pos["id"],
        pair=f"{t0['symbol']}/{t1['symbol']}",
        token0_symbol=t0["symbol"],
        token1_symbol=t1["symbol"],
        fee_tier=f"{fee_tier:.2f}%",
        price_lower=round(price_lower, 6),
        price_upper=round(price_upper, 6),
        current_price=round(current_price, 6),
        status=status,
        amount0=round(amount0, 6),
        amount1=round(amount1, 6),
        value_usd=round(value_usd, 2),
        deposited0=round(deposited0, 6),
        deposited1=round(deposited1, 6),
        fees0=round(fees0, 6),
        fees1=round(fees1, 6),
        fees_usd=round(fees_usd, 2),
        _deposited_usd=round(deposited_usd, 2),
    )


STATUS_EMOJI = {
    "in_range": "🟢 In range",
    "below":    "🔴 Below range",
    "above":    "🔵 Above range",
}


def format_positions(positions: list[LPPosition], wallet: str) -> str:
    if not positions:
        return f"No positions found for wallet <code>{wallet[:10]}…</code>"

    total_value = sum(p.value_usd for p in positions)
    total_fees = sum(p.fees_usd for p in positions)

    lines = [
        f"<b>LP Positions — {wallet[:8]}…{wallet[-4:]}</b>",
        f"Total positions: <b>{len(positions)}</b>",
        f"Total value: <b>${total_value:,.2f}</b>",
        f"Collected fees: <b>${total_fees:,.2f}</b>",
        "",
    ]

    for i, pos in enumerate(positions, 1):
        status_str = STATUS_EMOJI.get(pos.status, pos.status)
        lines += [
            f"<b>#{i} {pos.pair} ({pos.fee_tier})</b>  <code>ID:{pos.token_id}</code>",
            f"  {status_str}",
            f"  Price:    <code>{pos.current_price}</code>",
            f"  Range:    <code>{pos.price_lower} – {pos.price_upper}</code>",
            f"  Balance:  <code>{pos.amount0} {pos.token0_symbol} / {pos.amount1} {pos.token1_symbol}</code>",
            f"  Value:    <b>${pos.value_usd:,.2f}</b>",
            f"  Fees:     <code>{pos.fees0} {pos.token0_symbol} + {pos.fees1} {pos.token1_symbol}</code>  (${pos.fees_usd:,.2f})",
            "",
        ]

    return "\n".join(lines)
