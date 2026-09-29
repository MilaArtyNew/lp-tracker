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
SWEEP = 0x14

PERMIT2 = Web3.to_checksum_address("0x000000000022D473030F116dDEE9F6B43aC78BA3")

V4_POSITION_MANAGER = {
    "ethereum": "0xbD216513d74c8cF14cF4747e6aAa6420ff64eE9E",
    "base":     "0x7C5f5A4bBd8fD63184577525326123B519429bDc",
    "arbitrum": "0xd88f38f930b7952f2db2432cb002e7abbf3dd869",
    "bnb":      "0x7a4a5c919ae2541aed11041a1aeee68f1287f95b",
    "robinhood": "0x58daec3116aae6d93017baaea7749052e8a04fa7",
}

V4_STATE_VIEW = {
    "ethereum": "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    "base":     "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
    "arbitrum": "0x76fd297e2d437cd7f76d50f01afe6160f86e9990",
    "bnb":      "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4",
    "robinhood": "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b",
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


# ── Approval helpers ─────────────────────────────────────────────────────────

_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)")


def _cache_minted_token(chain: str, wallet: str, posm_addr: str, receipt: dict) -> None:
    """Parse Transfer event from receipt to get token ID, save to tracker cache."""
    try:
        posm_lower = posm_addr.lower()
        for log_entry in receipt.get("logs", []):
            topics = log_entry.get("topics", [])
            if (log_entry["address"].lower() == posm_lower
                    and len(topics) >= 4
                    and topics[0] == _TRANSFER_SIG):
                token_id = int(topics[3].hex(), 16)
                block_num = receipt["blockNumber"]
                from modules.rpc_tracker import cache_v4_position
                cache_v4_position(chain, wallet, token_id, block_num)
                return
    except Exception:
        pass

def _send_tx(w3: Web3, account, tx: dict, progress_cb: Callable[[str], None] | None = None) -> tuple[str, dict]:
    """Sign, send, wait for tx. Returns (tx_hash_hex, receipt)."""
    tx["nonce"] = w3.eth.get_transaction_count(account.address, "pending")
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
        tx["maxFeePerGas"] = int(base * 1.25) + priority  # 25% buffer over base fee

    for attempt in range(3):
        try:
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            break
        except Exception as e:
            if "nonce too low" in str(e) and attempt < 2:
                tx["nonce"] = w3.eth.get_transaction_count(account.address, "pending")
                continue
            raise
    if progress_cb:
        progress_cb(f"📤 Tx sent: `{tx_hash.hex()}`")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        raise RuntimeError(f"Tx reverted: {tx_hash.hex()}")
    return tx_hash.hex(), receipt


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
        }, progress_cb)  # receipt discarded

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
        }, progress_cb)  # receipt discarded


# ── Position opening ──────────────────────────────────────────────────────────

def _build_mint_calldata(
    currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str,
    tick_lower: int, tick_upper: int,
    liquidity: int, amount0_max: int, amount1_max: int,
    recipient: str,
) -> bytes:
    """Encode modifyLiquidities unlockData for a single MINT_POSITION + SETTLE_PAIR.
    Если currency0 — нативный токен (0x0), добавляется SWEEP: неизрасходованный ETH вернётся получателю."""
    native0 = currency0.lower() == "0x" + "0" * 40
    actions = bytes([MINT_POSITION, SETTLE_PAIR] + ([SWEEP] if native0 else []))

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
    params = [mint_params, settle_params]
    if native0:
        params.append(abi_encode(["address", "address"], ["0x" + "0" * 40, Web3.to_checksum_address(recipient)]))

    return abi_encode(["bytes", "bytes[]"], [actions, params])


def build_level_tx(posm_addr: str, currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str,
                   plan, recipient: str, deadline: int) -> dict:
    """tx-параметры (to/data/value) для одного уровня. Нативный currency0 => value = amount0_max."""
    unlock = _build_mint_calldata(currency0, currency1, fee, tick_spacing, hooks,
                                  plan.tick_lower, plan.tick_upper, plan.liquidity,
                                  plan.amount0_max, plan.amount1_max, recipient)
    from eth_abi import encode as _enc
    data = "0x" + Web3.keccak(text="modifyLiquidities(bytes,uint256)")[:4].hex() + _enc(["bytes", "uint256"], [unlock, deadline]).hex()
    native0 = currency0.lower() == "0x" + "0" * 40
    return {"to": Web3.to_checksum_address(posm_addr), "data": data, "value": plan.amount0_max if native0 else 0}


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
    levels: list[dict],          # [{"lower": float, "upper": float, "amount": usd}] — цены «котировка за актив»
    slippage: float = 0.05,
    progress_cb: Callable[[str], None] | None = None,
    *,
    quote_addr: str,             # токен, в котором вносится депозит (ETH/USDT/USDG…); второй токен пары — актив
    usd_per_quote: float,        # USD-цена одной единицы котировки (для стейбла ~1, для ETH — курс)
) -> list[dict]:
    """
    Open multiple LP positions from a bid ladder (ranges below the asset price, deposit in the quote token).

    Ориентация не зависит от порядка адресов: quote_addr может быть и currency0 (нативный ETH всегда им и является),
    и currency1. Уровни задаются как «котировка за актив», amount — USD. Возвращает [{"level", "tx", "error"}].
    """
    from modules.ladder_plan import plan_ladder

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
        raise RuntimeError(f"Pool not found or getSlot0 failed: {e}")

    if sqrt_cur == 0:
        raise RuntimeError("Pool not initialized (sqrtPrice=0) — check fee tier or tick spacing")

    q = quote_addr.lower()
    if q not in (currency0.lower(), currency1.lower()):
        raise ValueError("quote_addr is neither currency0 nor currency1 of the pool")
    quote_is_c1 = q == currency1.lower()

    price_raw = (sqrt_cur / Q96) ** 2
    pool_price = price_raw * (10 ** dec0) / (10 ** dec1)               # token1 / token0
    asset_price = pool_price if quote_is_c1 else 1.0 / pool_price      # котировка за актив
    if progress_cb:
        progress_cb(f"📍 Price: `{asset_price:.6g}` quote per asset (fee={fee}, tick_spacing={tick_spacing})")

    plans = plan_ladder(sqrt_cur=sqrt_cur, dec0=dec0, dec1=dec1, tick_spacing=tick_spacing, levels=levels,
                        quote_is_c1=quote_is_c1, usd_per_quote=usd_per_quote, slippage=slippage)
    ok_plans = [pl for pl in plans if pl.error is None]
    need_max = {currency0.lower(): sum(pl.amount0_max for pl in ok_plans),
                currency1.lower(): sum(pl.amount1_max for pl in ok_plans)}

    zero = "0x0000000000000000000000000000000000000000"
    # Балансы проверяем ДО первой транзакции: иначе аппрувы уйдут, а mint откатится
    for tok_addr, dec in [(currency0, dec0), (currency1, dec1)]:
        need = need_max[tok_addr.lower()]
        if need == 0:
            continue
        if tok_addr.lower() == zero:
            bal = w3.eth.get_balance(account.address)
            if bal < need:
                raise RuntimeError(f"Not enough native balance: need ≥ {need / 10 ** dec:.6f}, have {bal / 10 ** dec:.6f} (plus gas)")
            continue
        tok = w3.eth.contract(address=Web3.to_checksum_address(tok_addr), abi=_ERC20_ABI)
        bal = tok.functions.balanceOf(account.address).call()
        if progress_cb:
            progress_cb(f"🔍 `{tok_addr[:8]}…`  bal={bal / 10 ** dec:.4f}  need≈{need / 10 ** dec:.4f}")
        if bal < need:
            raise RuntimeError(f"Not enough token `{tok_addr[:8]}…`: need ≥ {need / 10 ** dec:.4f}, have {bal / 10 ** dec:.4f}")
        ensure_permit2_approved(w3, account, tok_addr, posm_addr, need, progress_cb)

    results = []
    for pl in plans:
        i = pl.index
        if pl.error:
            results.append({"level": i, "tx": None, "error": pl.error})
            if progress_cb:
                progress_cb(f"❌ Level #{i} skipped: {pl.error}")
            continue
        try:
            lvl = levels[i - 1]
            if progress_cb:
                progress_cb(f"⏳ Opening level #{i}: ${lvl['amount']:,.2f} "
                            f"[{lvl['lower']:.6g} – {lvl['upper']:.6g}] ticks [{pl.tick_lower},{pl.tick_upper}]…")
            tx = build_level_tx(posm_addr, currency0, currency1, fee, tick_spacing, hooks, pl,
                                account.address, int(time.time()) + 300)
            tx_hash, receipt = _send_tx(w3, account, {"from": account.address, **tx})
            _cache_minted_token(chain, account.address, posm_addr, receipt)
            results.append({"level": i, "tx": tx_hash, "error": None})
            if progress_cb:
                progress_cb(f"✅ Level #{i} opened: `{tx_hash}`")
        except Exception as e:
            results.append({"level": i, "tx": None, "error": str(e)})
            if progress_cb:
                progress_cb(f"❌ Level #{i} error: {e}")

    return results
