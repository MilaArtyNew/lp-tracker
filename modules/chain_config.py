"""Shared chain constants and pool price fetcher."""
import logging
from web3 import Web3
from config import RPC_URLS

log = logging.getLogger(__name__)

Q96 = 2 ** 96
ZERO_ADDR = "0x0000000000000000000000000000000000000000"

KNOWN_TOKENS: dict[str, dict[str, str]] = {
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "USDT": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
        "ETH":  ZERO_ADDR,
        "WETH": "0x4200000000000000000000000000000000000006",
        "DAI":  "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "ETH":  ZERO_ADDR,
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "DAI":  "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "arbitrum": {
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "ETH":  ZERO_ADDR,
        "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "DAI":  "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    "bnb": {
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "ETH":  ZERO_ADDR,
        "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "DAI":  "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
    },
}

FEE_TIERS = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}
TICK_SPACINGS = {100: 1, 500: 10, 3000: 60, 10000: 200}
CHAINS = ["base", "bnb", "ethereum", "arbitrum"]

_STATE_VIEW = {
    "ethereum": "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    "base":     "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
    "arbitrum": "0x76fd297e2d437cd7f76d50f01afe6160f86e9990",
    "bnb":      "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4",
}

_SV_ABI = [{
    "inputs": [{"name": "poolId", "type": "bytes32"}],
    "name": "getSlot0",
    "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"},
        {"name": "tick",         "type": "int24"},
        {"name": "protocolFee",  "type": "uint24"},
        {"name": "lpFee",        "type": "uint24"},
    ],
    "stateMutability": "view",
    "type": "function",
}]

_DEC_ABI = [{
    "inputs": [], "name": "decimals",
    "outputs": [{"type": "uint8"}],
    "stateMutability": "view", "type": "function",
}]


def resolve_addr(token: str, chain: str) -> str | None:
    if token.startswith("0x") and len(token) == 42:
        return token
    return KNOWN_TOKENS.get(chain, {}).get(token.upper())


def _decimals(w3: Web3, addr: str) -> int:
    if addr.lower() == ZERO_ADDR:
        return 18
    c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=_DEC_ABI)
    return c.functions.decimals().call()


def get_pool_price(chain: str, token: str, quote: str, fee: int) -> float | None:
    """Return pool price as (quote per token) in human units, or None."""
    from modules.v4_executor import compute_pool_id

    token_addr = resolve_addr(token, chain)
    quote_addr = resolve_addr(quote, chain)
    if not token_addr or not quote_addr:
        log.warning("get_pool_price: cannot resolve %s/%s on %s", token, quote, chain)
        return None

    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URLS[chain]))
        a = Web3.to_checksum_address(token_addr)
        b = Web3.to_checksum_address(quote_addr)
        c0, c1 = (a, b) if a.lower() < b.lower() else (b, a)

        tick_spacing = TICK_SPACINGS.get(fee, max(1, fee // 50))
        pool_id = compute_pool_id(c0, c1, fee, tick_spacing, ZERO_ADDR)

        sv = w3.eth.contract(
            address=Web3.to_checksum_address(_STATE_VIEW[chain]), abi=_SV_ABI
        )
        sqrt_price = sv.functions.getSlot0(pool_id).call()[0]
        if sqrt_price == 0:
            log.warning("get_pool_price: sqrtPrice=0, pool may not exist")
            return None

        dec0 = _decimals(w3, c0)
        dec1 = _decimals(w3, c1)

        # price_raw = c1_raw / c0_raw = (sqrtPriceX96/Q96)^2
        price_raw = (sqrt_price / Q96) ** 2
        # human: (c1/c0) adjusted for decimals
        price_c1_per_c0 = price_raw * (10 ** dec0) / (10 ** dec1)

        # Return as quote_per_token
        if a.lower() == c0.lower():
            return price_c1_per_c0          # token=c0, quote=c1
        else:
            return 1.0 / price_c1_per_c0   # token=c1, quote=c0

    except Exception as e:
        log.warning("get_pool_price error: %s", e)
        return None
