import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

_ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY", "").strip()

def _rpc(alchemy_slug: str, public_fallback: str) -> str:
    if _ALCHEMY_KEY:
        return f"https://{alchemy_slug}.g.alchemy.com/v2/{_ALCHEMY_KEY}"
    return public_fallback

RPC_URLS = {
    "ethereum": _rpc("eth-mainnet",  "https://eth.llamarpc.com"),
    "arbitrum": _rpc("arb-mainnet",  "https://arb1.arbitrum.io/rpc"),
    "base":     _rpc("base-mainnet", "https://mainnet.base.org"),
    "bnb":      _rpc("bnb-mainnet",  "https://bsc.publicnode.com"),
}

# Archive-capable RPCs for historical state queries (eth_call at past blocks)
ARCHIVE_RPC_URLS = {
    "ethereum": _rpc("eth-mainnet",  "https://eth.llamarpc.com"),
    "arbitrum": _rpc("arb-mainnet",  "https://arb1.arbitrum.io/rpc"),
    "base":     _rpc("base-mainnet", "https://mainnet.base.org"),
    "bnb":      _rpc("bnb-mainnet",  "https://bsc.publicnode.com"),
}

COINGECKO_API = "https://api.coingecko.com/api/v3"

LADDER_LEVELS = {
    "aggressive":   4,
    "optimal":      6,
    "conservative": 10,
}

LADDER_WEIGHTS = {
    "aggressive":   [15, 20, 27, 38],
    "optimal":      [10, 12, 14, 17, 21, 26],
    "conservative": [6, 7, 8, 8, 9, 10, 11, 12, 14, 15],
}
