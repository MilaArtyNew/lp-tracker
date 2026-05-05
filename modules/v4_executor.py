"""
Uniswap v4 position opener.
Opens LP positions using PositionManager.modifyLiquidities (MINT_POSITION + SETTLE_PAIR).
Tokens must be approved via Permit2 before calling.
"""
import math
import time
from typing import Callable

from eth_abi import encode as abi_encode
from web3 import Web3

from config import RPC_URLS

Q96 = 2 ** 96

# Uniswap v4 Actions
MINT_POSITION = 2
SETTLE_PAIR = 13

PERMIT2 = Web3.to_checksum_address("0x000000000022D473030F116dDEE9F6B43aC78BA3")

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

_PERMIT2_ABI = [
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [
            {"name": "amount", "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
            {"name": "nonce", "type": "uint48"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
        ],
        "name": "approve",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

_ERC20_ABI = [
    {
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_POSM_ABI = [
    {
        "inputs": [
            {"name": "unlockData", "type": "bytes"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "modifyLiquidities",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    }
]

_STATE_VIEW_ABI = [
    {
        "inputs": [{"name": "poolId", "type": "bytes32"}],
        "name": "getSlot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "protocolFee", "type": "uint24"},
            {"name": "lpFee", "type": "uint24"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


# ── Math helpers ─────────────────────────────────────────────────────────────

def price_to_tick(price: float, dec0: int, dec1: int) -> int:
    """Human price (token1/token0) → Uniswap tick."""
    raw = price * (10 ** dec1) / (10 ** dec0)
    return math.floor(math.log(raw) / math.log(1.0001))


def round_tick(tick: int, tick_spacing: int) -> int:
    """Round tick to nearest multiple of tick_spacing."""
    return round(tick / tick_spacing) * tick_spacing


def tick_to_sqrt_x96(tick: int) -> int:
    return int(math.sqrt(1.0001 ** tick) * Q96)


def liquidity_for_amount0(sqrt_lower: int, sqrt_upper: int, amount0: int) -> int:
    """Liquidity when position is entirely in token0 (above current price range)."""
    return amount0 * sqrt_lower * sqrt_upper // ((sqrt_upper - sqrt_lower) * Q96)


def liquidity_for_amounts(sqrt_cur: int, sqrt_lower: int, sqrt_upper: int,
                          amount0: int, amount1: int) -> int:
    if sqrt_cur <= sqrt_lower:
        return liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
    elif sqrt_cur >= sqrt_upper:
        return amount1 * Q96 // (sqrt_upper - sqrt_lower)
    else:
        l0 = amount0 * sqrt_cur * sqrt_upper // ((sqrt_upper - sqrt_cur) * Q96)
        l1 = amount1 * Q96 // (sqrt_cur - sqrt_lower)
        return min(l0, l1)


def compute_pool_id(currency0: str, currency1: str, fee: int,
                    tick_spacing: int, hooks: str) -> bytes:
    encoded = abi_encode(
        ["address", "address", "uint24", "int24", "address"],
        [Web3.to_checksum_address(currency0),
         Web3.to_checksum_address(currency1),
         fee, tick_spacing,
         Web3.to_checksum_address(hooks)],
    )
    return Web3.keccak(encoded)


# ── Approval helpers ──────────────────────────────────────────────────────────

def _send_tx(w3: Web3, account, tx: dict, progress_cb: Callable[[str], None] | None = None) -> str:
    """Sign, send, wait for tx. Returns tx hash."""
    tx["nonce"] = w3.eth.get_transaction_count(account.address)
    tx["chainId"] = w3.eth.chain_id
    if "gas" not in tx:
        tx["gas"] = w3.eth.estimate_gas({k: v for k, v in tx.items() if k != "gas"})
    if "maxFeePerGas" not in tx and "gasPrice" not in tx:
        base = w3.eth.gas_price
        try:
            priority = w3.eth.max_priority_fee
        except Exception:
            priority = int(base * 0.1)
        priority = max(priority, 100_000_000)  # min 0.1 gwei
        tx["maxPriorityFeePerGas"] = priority
        tx["maxFeePerGas"] = base + priority

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    if progress_cb:
        progress_cb(f"📤 Tx отправлен: `{tx_hash.hex()}`")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError(f"Tx reverted: {tx_hash.hex()}")
    return tx_hash.hex()


def ensure_permit2_approved(w3: Web3, account, token_addr: str, posm_addr: str,
                            amount: int, progress_cb: Callable[[str], None] | None = None) -> None:
    """Ensure Permit2 has allowance from wallet, and PositionManager has Permit2 allowance."""
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    permit2 = w3.eth.contract(address=PERMIT2, abi=_PERMIT2_ABI)
    posm_cs = Web3.to_checksum_address(posm_addr)

    # 1. ERC20 → Permit2
    erc20_allowance = token.functions.allowance(account.address, PERMIT2).call()
    if erc20_allowance < amount:
        if progress_cb:
            progress_cb("🔑 Approving Permit2 for token…")
        _send_tx(w3, account, {
            "from": account.address,
            "to": Web3.to_checksum_address(token_addr),
            "data": token.encode_abi("approve", [PERMIT2, 2**256 - 1]),
            "value": 0,
        }, progress_cb)

    # 2. Permit2 → PositionManager
    p2_amt, p2_exp, _ = permit2.functions.allowance(account.address, token_addr, posm_cs).call()
    if p2_amt < amount or p2_exp < int(time.time()) + 3600:
        if progress_cb:
            progress_cb("🔑 Approving PositionManager via Permit2…")
        expiration = int(time.time()) + 30 * 24 * 3600  # 30 days
        _send_tx(w3, account, {
            "from": account.address,
            "to": PERMIT2,
            "data": permit2.encode_abi("approve", [
                Web3.to_checksum_address(token_addr),
                posm_cs,
                2**160 - 1,
                expiration,
            ]),
            "value": 0,
        }, progress_cb)


# ── Position opening ──────────────────────────────────────────────────────────

def _build_mint_calldata(
    currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str,
    tick_lower: int, tick_upper: int,
    liquidity: int, amount0_max: int, amount1_max: int,
    recipient: str,
) -> bytes:
    """Encode modifyLiquidities unlockData for a single MINT_POSITION + SETTLE_PAIR."""
    actions = bytes([MINT_POSITION, SETTLE_PAIR])

    pool_key = (
        Web3.to_checksum_address(currency0),
        Web3.to_checksum_address(currency1),
        fee,
        tick_spacing,
        Web3.to_checksum_address(hooks),
    )

    mint_params = abi_encode(
        ["(address,address,uint24,int24,address)", "int24", "int24",
         "uint256", "uint128", "uint128", "address", "bytes"],
        [pool_key, tick_lower, tick_upper,
         liquidity, amount0_max, amount1_max,
         Web3.to_checksum_address(recipient), b""],
    )

    settle_params = abi_encode(
        ["address", "address"],
        [Web3.to_checksum_address(currency0), Web3.to_checksum_address(currency1)],
    )

    return abi_encode(["bytes", "bytes[]"], [actions, [mint_params, settle_params]])


def open_ladder_positions(
    chain: str,
    account,
    currency0: str,
    currency1: str,
    fee: int,
    tick_spacing: int,
    hooks: str,
    dec0: int,
    dec1: int,
    levels: list[dict],          # [{"lower": float, "upper": float, "amount": float}]
    slippage: float = 0.05,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Open multiple LP positions from a ladder.
    Each level: {"lower": price_lower, "upper": price_upper, "amount": usd_amount}
    Returns list of {"level": i, "tx": hash, "error": str|None}
    """
    w3 = Web3(Web3.HTTPProvider(RPC_URLS[chain]))
    posm_addr = Web3.to_checksum_address(V4_POSITION_MANAGER[chain])
    sv_addr = Web3.to_checksum_address(V4_STATE_VIEW[chain])

    posm = w3.eth.contract(address=posm_addr, abi=_POSM_ABI)
    sv = w3.eth.contract(address=sv_addr, abi=_STATE_VIEW_ABI)

    pool_id = compute_pool_id(currency0, currency1, fee, tick_spacing, hooks)

    try:
        slot0 = sv.functions.getSlot0(pool_id).call()
        sqrt_cur = slot0[0]
    except Exception as e:
        raise RuntimeError(f"Пул не найден или getSlot0 упал: {e}")

    zero = "0x0000000000000000000000000000000000000000"
    dec_map = {currency0.lower(): dec0, currency1.lower(): dec1}

    # Check balances and current allowances, then approve
    total_usd = sum(lvl["amount"] for lvl in levels)
    for tok_addr, dec in [(currency0, dec0), (currency1, dec1)]:
        if tok_addr.lower() == zero:
            continue
        tok = w3.eth.contract(address=Web3.to_checksum_address(tok_addr), abi=_ERC20_ABI)
        bal = tok.functions.balanceOf(account.address).call()
        p2_allow = tok.functions.allowance(account.address, PERMIT2).call()
        bal_h = bal / 10 ** dec
        p2_h = p2_allow / 10 ** dec
        if progress_cb:
            progress_cb(
                f"🔍 `{tok_addr[:8]}…`  bal={bal_h:.4f}  permit2_allow={p2_h:.2f}"
            )
        if bal == 0:
            if progress_cb:
                progress_cb(f"⚠️ `{tok_addr[:8]}…` — баланс 0, approve пропущен")
            continue  # skip approve; if token is needed on-chain → TRANSFER_FROM_FAILED

        total_amount = int(total_usd * 10 ** dec * 2)
        ensure_permit2_approved(w3, account, tok_addr, posm_addr, total_amount, progress_cb)

    results = []
    for i, lvl in enumerate(levels, 1):
        try:
            tick_lower = round_tick(price_to_tick(lvl["lower"], dec0, dec1), tick_spacing)
            tick_upper = round_tick(price_to_tick(lvl["upper"], dec0, dec1), tick_spacing)

            if tick_lower >= tick_upper:
                raise ValueError(f"tick_lower={tick_lower} >= tick_upper={tick_upper}")

            sqrt_lower = tick_to_sqrt_x96(tick_lower)
            sqrt_upper = tick_to_sqrt_x96(tick_upper)

            amount0_raw = int(lvl["amount"] * 10 ** dec0)
            amount1_raw = int(lvl["amount"] * 10 ** dec1)

            # Determine which token is needed
            if sqrt_cur <= sqrt_lower:
                direction = "only token0"
            elif sqrt_cur >= sqrt_upper:
                direction = "only token1"
            else:
                direction = "both tokens"

            if progress_cb:
                progress_cb(
                    f"📐 lvl#{i}: ticks [{tick_lower},{tick_upper}] "
                    f"sqrt_cur={sqrt_cur:.3e} lo={sqrt_lower:.3e} hi={sqrt_upper:.3e} "
                    f"→ {direction}"
                )

            liquidity = liquidity_for_amounts(sqrt_cur, sqrt_lower, sqrt_upper,
                                              amount0_raw, amount1_raw)
            if liquidity == 0:
                raise ValueError("Нулевая ликвидность")

            amount0_max = int(amount0_raw * (1 + slippage))
            amount1_max = int(amount1_raw * (1 + slippage))

            unlock_data = _build_mint_calldata(
                currency0, currency1, fee, tick_spacing, hooks,
                tick_lower, tick_upper,
                liquidity, amount0_max, amount1_max,
                account.address,
            )

            deadline = int(time.time()) + 300

            if progress_cb:
                progress_cb(f"⏳ Открываю уровень #{i}: ${lvl['amount']:,.2f} "
                            f"[{lvl['lower']:.4f} – {lvl['upper']:.4f}]…")

            tx_hash = _send_tx(w3, account, {
                "from": account.address,
                "to": posm_addr,
                "data": posm.encode_abi("modifyLiquidities", [unlock_data, deadline]),
                "value": 0,
            }, progress_cb)

            results.append({"level": i, "tx": tx_hash, "error": None})
            if progress_cb:
                progress_cb(f"✅ Уровень #{i} открыт: `{tx_hash}`")

        except Exception as e:
            results.append({"level": i, "tx": None, "error": str(e)})
            if progress_cb:
                progress_cb(f"❌ Уровень #{i} ошибка: {e}")

    return results
