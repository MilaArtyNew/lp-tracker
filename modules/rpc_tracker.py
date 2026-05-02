import asyncio
import concurrent.futures
import json
import os
from eth_abi import encode as abi_encode
from web3 import Web3
from config import RPC_URLS, ARCHIVE_RPC_URLS
from modules.uniswap_math import get_amounts, sqrt_price_x96_to_price, position_status
from modules.position_tracker import LPPosition
from utils.price import get_price_coingecko

# (nfpm_address, factory_address) pairs per chain
PROTOCOLS: dict[str, list[tuple[str, str]]] = {
    "ethereum": [
        ("0xC36442b4a4522E871399CD717aBDD847Ab11FE88", "0x1F98431c8aD98523631AE4a59f267346ea31F984"),
    ],
    "arbitrum": [
        ("0xC36442b4a4522E871399CD717aBDD847Ab11FE88", "0x1F98431c8aD98523631AE4a59f267346ea31F984"),
    ],
    "base": [
        ("0x03a520b32C04BF3bEEf7BEb72E919cF822Ed34f1", "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"),  # Uniswap v3
        ("0x46A15B0b27311cedF172AB29E4f4766fbE7F4364", "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),  # PancakeSwap v3
    ],
    "bnb": [
        ("0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613", "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F"),
    ],
}

NFPM_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "index", "type": "uint256"}],
     "name": "tokenOfOwnerByIndex",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "positions",
     "outputs": [
         {"name": "nonce", "type": "uint96"}, {"name": "operator", "type": "address"},
         {"name": "token0", "type": "address"}, {"name": "token1", "type": "address"},
         {"name": "fee", "type": "uint24"}, {"name": "tickLower", "type": "int24"},
         {"name": "tickUpper", "type": "int24"}, {"name": "liquidity", "type": "uint128"},
         {"name": "feeGrowthInside0LastX128", "type": "uint256"},
         {"name": "feeGrowthInside1LastX128", "type": "uint256"},
         {"name": "tokensOwed0", "type": "uint128"}, {"name": "tokensOwed1", "type": "uint128"},
     ], "stateMutability": "view", "type": "function"},
]

FACTORY_ABI = [
    {"inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"},
                {"name": "fee", "type": "uint24"}],
     "name": "getPool", "outputs": [{"name": "pool", "type": "address"}],
     "stateMutability": "view", "type": "function"},
]

POOL_ABI = [
    {"inputs": [], "name": "slot0",
     "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
                 {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"},
                 {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"},
                 {"name": "unlocked", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
]

ERC20_ABI = [
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]

# Uniswap v4 — singleton PoolManager + StateView architecture
V4_POSITION_MANAGER = {
    "ethereum": "0xbD216513d74c8cF14cF4747e6aAa6420ff64eE9E",
    "base":     "0x7C5f5A4bBd8fD63184577525326123B519429bDc",
    "arbitrum": "0xd88f38f930b7952f2db2432cb002e7abbf3dd869",
    "bnb":      "0x7a4a5c919ae2541aed11041a1aeee68f1287f95b",
}

V4_STATE_VIEW = {
    "ethereum": "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    "base":     "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
    "arbitrum": "0x76fd297e2d437cd7f76d50f01afe6160f86e9990",
    "bnb":      "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4",
}

# Approximate v4 deployment block per chain (January 30, 2025)
_V4_DEPLOY_BLOCK = {
    "ethereum": 21_500_000,
    "base":     24_000_000,
    "arbitrum": 295_000_000,
    "bnb":      45_000_000,
}

# v4 PositionManager: no tokenOfOwnerByIndex — enumeration via Transfer events
V4_PM_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "ownerOf",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    # PositionInfo packed uint256: poolId_bytes25[top 200 bits] | tickUpper[bits 55..32] | tickLower[bits 31..8] | flags[7..0]
    {"inputs": [{"name": "tokenId", "type": "uint256"}],
     "name": "getPoolAndPositionInfo",
     "outputs": [
         {"components": [
             {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
             {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"},
             {"name": "hooks", "type": "address"},
         ], "name": "poolKey", "type": "tuple"},
         {"name": "info", "type": "uint256"},
     ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getPositionLiquidity",
     "outputs": [{"name": "liquidity", "type": "uint128"}], "stateMutability": "view", "type": "function"},
]

V4_STATE_VIEW_ABI = [
    {"inputs": [{"name": "poolId", "type": "bytes32"}], "name": "getSlot0",
     "outputs": [
         {"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
         {"name": "protocolFee", "type": "uint24"}, {"name": "lpFee", "type": "uint24"},
     ], "stateMutability": "view", "type": "function"},
    {"inputs": [
         {"name": "poolId", "type": "bytes32"},
         {"name": "owner", "type": "address"},
         {"name": "tickLower", "type": "int24"},
         {"name": "tickUpper", "type": "int24"},
         {"name": "salt", "type": "bytes32"},
     ], "name": "getPositionInfo",
     "outputs": [
         {"name": "liquidity", "type": "uint128"},
         {"name": "feeGrowthInside0LastX128", "type": "uint256"},
         {"name": "feeGrowthInside1LastX128", "type": "uint256"},
     ], "stateMutability": "view", "type": "function"},
    {"inputs": [
         {"name": "poolId", "type": "bytes32"},
         {"name": "tickLower", "type": "int24"},
         {"name": "tickUpper", "type": "int24"},
     ], "name": "getFeeGrowthInside",
     "outputs": [
         {"name": "feeGrowthInside0X128", "type": "uint256"},
         {"name": "feeGrowthInside1X128", "type": "uint256"},
     ], "stateMutability": "view", "type": "function"},
]

_V4_NATIVE_SYMBOL = {"ethereum": "ETH", "base": "ETH", "arbitrum": "ETH", "bnb": "BNB"}
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_V4_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()
_V4_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _v4_cache_path(chain: str, wallet: str) -> str:
    return os.path.join(_V4_CACHE_DIR, f"v4_{chain}_{wallet.lower()[2:]}.json")


def _load_v4_cache(chain: str, wallet: str) -> dict:
    path = _v4_cache_path(chain, wallet)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
                data.setdefault("acq_blocks", {})
                return data
        except Exception:
            pass
    return {"last_block": None, "token_ids": [], "acq_blocks": {}}


def _save_v4_cache(chain: str, wallet: str, data: dict):
    os.makedirs(_V4_CACHE_DIR, exist_ok=True)
    with open(_v4_cache_path(chain, wallet), "w") as f:
        json.dump(data, f)


def _decode_v4_ticks(info: int) -> tuple[int, int]:
    """Extract tickLower and tickUpper from packed PositionInfo uint256.

    Layout (from v4-periphery source):
      bits [255..56]  = poolId (bytes25, top 25 bytes)
      bits [55..32]   = tickUpper (int24)
      bits [31..8]    = tickLower (int24)
      bits [7..0]     = flags
    """
    s24 = lambda v: (v & 0xFFFFFF) if (v & 0xFFFFFF) < 2 ** 23 else (v & 0xFFFFFF) - 2 ** 24
    return s24(info >> 8), s24(info >> 32)  # (tickLower, tickUpper)


def _compute_v4_pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> bytes:
    """keccak256(abi.encode(poolKey)) — matches Solidity PoolIdLibrary.toId()."""
    encoded = abi_encode(
        ["address", "address", "uint24", "int24", "address"],
        [
            Web3.to_checksum_address(currency0),
            Web3.to_checksum_address(currency1),
            fee,
            tick_spacing,
            Web3.to_checksum_address(hooks),
        ],
    )
    return Web3.keccak(encoded)


def _get_v4_token_ids(wallet_cs: str, pm_addr: str, w3: Web3, balance: int, chain: str) -> tuple[list[int], dict[int, int]]:
    """Enumerate v4 LP token IDs via Transfer events + per-wallet cache.

    Returns (token_ids, acq_blocks) where acq_blocks maps tokenId → acquisition block number.
    """
    if balance == 0:
        return [], {}

    pm_cs = Web3.to_checksum_address(pm_addr)
    wallet_padded = "0x" + "0" * 24 + wallet_cs[2:].lower()
    cur = w3.eth.block_number
    deploy_block = _V4_DEPLOY_BLOCK.get(chain, max(0, cur - 25_000_000))

    cache = _load_v4_cache(chain, wallet_cs)
    from_block = (cache["last_block"] + 1) if cache["last_block"] else max(deploy_block, cur - 25_000_000)
    known_ids: set[int] = set(cache["token_ids"])
    # acq_blocks: tokenId → most recent block where wallet received this token
    known_acq: dict[int, int] = {int(k): v for k, v in cache.get("acq_blocks", {}).items()}

    CHUNK = 50_000
    WORKERS = 3

    if from_block <= cur:
        ranges = []
        b = cur
        while b >= from_block:
            ranges.append((max(from_block, b - CHUNK + 1), b))
            b -= CHUNK

        def fetch_chunk(from_b: int, to_b: int) -> list:
            try:
                return w3.eth.get_logs({
                    "fromBlock": from_b, "toBlock": to_b,
                    "address": pm_cs,
                    "topics": [_V4_TRANSFER_SIG, None, wallet_padded],
                })
            except Exception:
                return []

        new_found: dict[int, int] = {}
        for batch_start in range(0, len(ranges), WORKERS):
            batch = ranges[batch_start: batch_start + WORKERS]
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for logs in ex.map(lambda r: fetch_chunk(*r), batch):
                    for log in logs:
                        tid = int(log["topics"][3].hex(), 16)
                        blk = log["blockNumber"]
                        # Keep the most recent acquisition block
                        if tid not in new_found or blk > new_found[tid]:
                            new_found[tid] = blk
            if len(known_ids | set(new_found)) >= balance:
                break

        known_ids.update(new_found.keys())
        known_acq.update(new_found)
        _save_v4_cache(chain, wallet_cs, {
            "last_block": cur,
            "token_ids": list(known_ids),
            "acq_blocks": {str(k): v for k, v in known_acq.items()},
        })

    # Verify current ownership (filters out transferred-away positions)
    pm_contract = w3.eth.contract(address=pm_cs, abi=V4_PM_ABI)
    result = []
    for tid in known_ids:
        try:
            if pm_contract.functions.ownerOf(tid).call().lower() == wallet_cs.lower():
                result.append(tid)
        except Exception:
            pass
    return result, known_acq


def _get_web3(chain: str) -> Web3:
    return Web3(Web3.HTTPProvider(RPC_URLS[chain]))


def _fetch_protocol_positions(
    wallet_cs: str, w3: Web3,
    nfpm_addr: str, factory_addr: str,
    token_cache: dict[str, tuple[str, int]],
) -> list[LPPosition]:
    nfpm = w3.eth.contract(address=Web3.to_checksum_address(nfpm_addr), abi=NFPM_ABI)
    factory = w3.eth.contract(address=Web3.to_checksum_address(factory_addr), abi=FACTORY_ABI)

    try:
        count = nfpm.functions.balanceOf(wallet_cs).call()
    except Exception:
        return []

    def get_token_info(addr: str) -> tuple[str, int]:
        if addr not in token_cache:
            try:
                t = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
                symbol = t.functions.symbol().call()
                decimals = t.functions.decimals().call()
                token_cache[addr] = (symbol, decimals)
            except Exception:
                token_cache[addr] = ("???", 18)
        return token_cache[addr]

    results = []
    for i in range(count):
        try:
            token_id = nfpm.functions.tokenOfOwnerByIndex(wallet_cs, i).call()
            pos = nfpm.functions.positions(token_id).call()

            liquidity = pos[7]
            if liquidity == 0:
                continue

            t0_addr = pos[2]
            t1_addr = pos[3]
            fee = pos[4]
            tick_lower = pos[5]
            tick_upper = pos[6]
            tokens_owed0 = pos[10]
            tokens_owed1 = pos[11]

            sym0, dec0 = get_token_info(t0_addr)
            sym1, dec1 = get_token_info(t1_addr)

            pool_addr = factory.functions.getPool(
                Web3.to_checksum_address(t0_addr),
                Web3.to_checksum_address(t1_addr),
                fee,
            ).call()

            pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=POOL_ABI)
            slot0 = pool.functions.slot0().call()
            sqrt_price = slot0[0]
            current_tick = slot0[1]

            current_price = sqrt_price_x96_to_price(sqrt_price, dec0, dec1)
            price_lower = sqrt_price_x96_to_price(
                int((1.0001 ** (tick_lower / 2)) * (2 ** 96)), dec0, dec1
            )
            price_upper = sqrt_price_x96_to_price(
                int((1.0001 ** (tick_upper / 2)) * (2 ** 96)), dec0, dec1
            )

            raw0, raw1 = get_amounts(liquidity, sqrt_price, tick_lower, tick_upper, current_tick)
            amount0 = raw0 / (10 ** dec0)
            amount1 = raw1 / (10 ** dec1)
            fees0 = tokens_owed0 / (10 ** dec0)
            fees1 = tokens_owed1 / (10 ** dec1)

            status = position_status(current_tick, tick_lower, tick_upper)

            results.append(LPPosition(
                token_id=str(token_id),
                pair=f"{sym0}/{sym1}",
                token0_symbol=sym0,
                token1_symbol=sym1,
                fee_tier=f"{fee / 10000:.2f}%",
                price_lower=round(price_lower, 8),
                price_upper=round(price_upper, 8),
                current_price=round(current_price, 8),
                status=status,
                amount0=round(amount0, 6),
                amount1=round(amount1, 6),
                value_usd=0.0,
                deposited0=0.0,
                deposited1=0.0,
                fees0=round(fees0, 6),
                fees1=round(fees1, 6),
                fees_usd=0.0,
                _deposited_usd=0.0,
                token0_address=t0_addr,
                token1_address=t1_addr,
            ))
        except Exception:
            continue

    return results


def _get_archive_web3(chain: str) -> Web3:
    url = ARCHIVE_RPC_URLS.get(chain, RPC_URLS[chain])
    return Web3(Web3.HTTPProvider(url))


def _fetch_v4_chain_positions(
    wallet_cs: str, w3: Web3, chain: str,
    token_cache: dict[str, tuple[str, int]],
) -> list[LPPosition]:
    pm_addr = V4_POSITION_MANAGER[chain]
    sv_addr = V4_STATE_VIEW[chain]

    pm = w3.eth.contract(address=Web3.to_checksum_address(pm_addr), abi=V4_PM_ABI)
    sv = w3.eth.contract(address=Web3.to_checksum_address(sv_addr), abi=V4_STATE_VIEW_ABI)
    sv_archive = _get_archive_web3(chain).eth.contract(
        address=Web3.to_checksum_address(sv_addr), abi=V4_STATE_VIEW_ABI
    )

    try:
        balance = pm.functions.balanceOf(wallet_cs).call()
    except Exception:
        return []

    token_ids, acq_blocks = _get_v4_token_ids(wallet_cs, pm_addr, w3, balance, chain)

    def get_token_info(addr: str) -> tuple[str, int]:
        key = addr.lower()
        if key not in token_cache:
            if key == _ZERO_ADDR:
                token_cache[key] = (_V4_NATIVE_SYMBOL.get(chain, "ETH"), 18)
            else:
                try:
                    t = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
                    token_cache[key] = (t.functions.symbol().call(), t.functions.decimals().call())
                except Exception:
                    token_cache[key] = ("???", 18)
        return token_cache[key]

    results = []
    for token_id in token_ids:
        try:
            pool_key, info = pm.functions.getPoolAndPositionInfo(token_id).call()
            liquidity = pm.functions.getPositionLiquidity(token_id).call()

            if liquidity == 0:
                continue

            currency0, currency1, fee, tick_spacing, hooks = pool_key
            tick_lower, tick_upper = _decode_v4_ticks(info)

            pool_id = _compute_v4_pool_id(currency0, currency1, fee, tick_spacing, hooks)

            slot0 = sv.functions.getSlot0(pool_id).call()
            sqrt_price, current_tick = slot0[0], slot0[1]

            sym0, dec0 = get_token_info(currency0)
            sym1, dec1 = get_token_info(currency1)

            current_price = sqrt_price_x96_to_price(sqrt_price, dec0, dec1)
            price_lower = sqrt_price_x96_to_price(
                int((1.0001 ** (tick_lower / 2)) * (2 ** 96)), dec0, dec1
            )
            price_upper = sqrt_price_x96_to_price(
                int((1.0001 ** (tick_upper / 2)) * (2 ** 96)), dec0, dec1
            )

            raw0, raw1 = get_amounts(liquidity, sqrt_price, tick_lower, tick_upper, current_tick)
            amount0 = raw0 / (10 ** dec0)
            amount1 = raw1 / (10 ** dec1)

            # Uncollected fees via StateView
            fees0 = fees1 = 0.0
            try:
                salt = token_id.to_bytes(32, "big")
                pm_cs_addr = Web3.to_checksum_address(pm_addr)
                pos_info = sv.functions.getPositionInfo(pool_id, pm_cs_addr, tick_lower, tick_upper, salt).call()
                fg_inside = sv.functions.getFeeGrowthInside(pool_id, tick_lower, tick_upper).call()
                Q128 = 2 ** 128
                MASK = 2 ** 256
                delta0 = (fg_inside[0] - pos_info[1]) % MASK
                delta1 = (fg_inside[1] - pos_info[2]) % MASK
                fees0 = (liquidity * delta0 // Q128) / (10 ** dec0)
                fees1 = (liquidity * delta1 // Q128) / (10 ** dec1)
            except Exception:
                pass

            # Deposited amounts: reconstruct from sqrtPrice at acquisition block (archive node)
            deposited0 = deposited1 = 0.0
            acq_block = acq_blocks.get(token_id)
            if acq_block:
                try:
                    slot0_hist = sv_archive.functions.getSlot0(pool_id).call(block_identifier=acq_block)
                    raw0_d, raw1_d = get_amounts(liquidity, slot0_hist[0], tick_lower, tick_upper, slot0_hist[1])
                    deposited0 = raw0_d / (10 ** dec0)
                    deposited1 = raw1_d / (10 ** dec1)
                except Exception:
                    pass

            status = position_status(current_tick, tick_lower, tick_upper)
            fee_label = f"{fee / 10000:.2f}%" if fee > 0 else "dynamic"

            results.append(LPPosition(
                token_id=str(token_id),
                pair=f"{sym0}/{sym1}",
                token0_symbol=sym0,
                token1_symbol=sym1,
                fee_tier=fee_label,
                price_lower=round(price_lower, 8),
                price_upper=round(price_upper, 8),
                current_price=round(current_price, 8),
                status=status,
                amount0=round(amount0, 6),
                amount1=round(amount1, 6),
                value_usd=0.0,
                deposited0=round(deposited0, 6),
                deposited1=round(deposited1, 6),
                fees0=round(fees0, 6),
                fees1=round(fees1, 6),
                fees_usd=0.0,
                _deposited_usd=0.0,
                token0_address=currency0,
                token1_address=currency1,
            ))
        except Exception:
            continue

    return results


def _fetch_chain_positions(wallet: str, chain: str) -> list[LPPosition]:
    w3 = _get_web3(chain)
    if not w3.is_connected():
        return []

    wallet_cs = Web3.to_checksum_address(wallet)
    token_cache: dict[str, tuple[str, int]] = {}
    results = []

    for nfpm_addr, factory_addr in PROTOCOLS[chain]:
        positions = _fetch_protocol_positions(wallet_cs, w3, nfpm_addr, factory_addr, token_cache)
        results.extend(positions)

    if chain in V4_POSITION_MANAGER:
        results.extend(_fetch_v4_chain_positions(wallet_cs, w3, chain, token_cache))

    return results


async def _enrich_usd(positions: list[LPPosition], chain: str) -> None:
    addrs = {p.token0_address.lower() for p in positions if p.token0_address} | \
            {p.token1_address.lower() for p in positions if p.token1_address}
    addr_list = list(addrs)
    results = await asyncio.gather(*[get_price_coingecko(a, chain) for a in addr_list], return_exceptions=True)
    prices = {a: r for a, r in zip(addr_list, results) if isinstance(r, float)}
    for p in positions:
        p0 = prices.get(p.token0_address.lower(), 0.0) if p.token0_address else 0.0
        p1 = prices.get(p.token1_address.lower(), 0.0) if p.token1_address else 0.0
        p.value_usd = round(p.amount0 * p0 + p.amount1 * p1, 2)
        p.fees_usd = round(p.fees0 * p0 + p.fees1 * p1, 2)
        if p._deposited_usd == 0.0 and (p.deposited0 > 0 or p.deposited1 > 0):
            p._deposited_usd = round(p.deposited0 * p0 + p.deposited1 * p1, 2)


async def fetch_positions_rpc(wallet: str, chain: str) -> list[LPPosition]:
    positions = await asyncio.to_thread(_fetch_chain_positions, wallet, chain)
    if positions:
        await _enrich_usd(positions, chain)
    return positions


async def fetch_all_chains_rpc(wallet: str) -> list[LPPosition]:
    tasks = [fetch_positions_rpc(wallet, chain) for chain in PROTOCOLS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    positions = []
    for r in results:
        if isinstance(r, list):
            positions.extend(r)
    return positions
