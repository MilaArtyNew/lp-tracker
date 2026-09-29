"""Симуляция лесенки на РЕАЛЬНЫХ v4-пулах через eth_call с подменой состояния (ничего не отправляется).
Запуск: BOT_TOKEN=x python tests/simulate_ladder.py   (нужен интернет)
Для ERC20 подбираются слоты баланса/allowance и подменяется Permit2-allowance; слот не найден -> SKIP, а не PASS."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eth_abi import encode
from web3 import Web3

from config import RPC_URLS
from modules.ladder_plan import Q96, plan_ladder
from modules.v4_executor import V4_POSITION_MANAGER, V4_STATE_VIEW, build_level_tx, compute_pool_id, PERMIT2

ZERO = "0x" + "0" * 40
FROM = Web3.to_checksum_address("0x" + "22" * 20)
# mainnet.base.org режет по лимиту при переборе слотов (429 выглядит как «слот не тот») — берём publicnode
SIM_RPC = {"base": "https://base-rpc.publicnode.com"}
ABI = [
    {"name": "poolKeys", "type": "function", "stateMutability": "view", "inputs": [{"name": "id", "type": "bytes25"}],
     "outputs": [{"name": "c0", "type": "address"}, {"name": "c1", "type": "address"}, {"name": "fee", "type": "uint24"},
                 {"name": "ts", "type": "int24"}, {"name": "hooks", "type": "address"}]},
    {"name": "getSlot0", "type": "function", "stateMutability": "view", "inputs": [{"name": "id", "type": "bytes32"}],
     "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"}, {"name": "pf", "type": "uint24"}, {"name": "lp", "type": "uint24"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"name": "o", "type": "address"}, {"name": "s", "type": "address"}], "outputs": [{"type": "uint256"}]},
]
P2_ABI = [{"name": "allowance", "type": "function", "stateMutability": "view",
           "inputs": [{"name": "o", "type": "address"}, {"name": "t", "type": "address"}, {"name": "s", "type": "address"}],
           "outputs": [{"name": "amount", "type": "uint160"}, {"name": "exp", "type": "uint48"}, {"name": "nonce", "type": "uint48"}]}]

POOLS = [  # (chain, poolId, описание) — реальные v4-пулы без хуков, ключ есть в PositionManager
    ("robinhood", "0x6d9af55c4cb0d740dc5237e8fe2d60e017412fb1af4e2d81e948124797e56748", "CRCL/ETH (нативный ETH = currency0)"),
    ("base", "0xb24f7fcd75c3a001b74be4c60f181c024154c4d7c33097777e3950979042b073", "tBTC/USDC (USDC = currency1)"),
    ("base", "0x8930762cccc36040f25fc29db58a8ec22e872a347260d992f39666a3cdce7e5a", "Basecat/USDC"),
    ("base", "0x4bebca556c1a4c336cca35c4723a2fc61567803412e58e3cc0d19e66cd57a273", "USDbC/USDT (обе ориентации котировки)"),
    ("robinhood", "0x7499938c352d5b5b8f0c648722aca5ee964ef9b85c3a3041f1ec379726291d9d", "AMC/USDG (USDG = currency1)"),
]
failed = skipped = 0


def keccak(b): return Web3.keccak(b)
def pad(x): return bytes.fromhex(x[2:]).rjust(32, b"\0") if isinstance(x, str) else int(x).to_bytes(32, "big")


def call_retry(fn, tries=4):
    """None — вызов не удался по существу (revert/не тот слот); 429 повторяем, чтобы лимит не выглядел как «слот не найден»."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            return None


def find_slot(w3, token, fn, args_builder, probe, max_slot=20):
    """Подбор индекса mapping-слота: подменяем значение и смотрим, увидел ли его view-вызов."""
    c = w3.eth.contract(address=token, abi=ABI)
    for s in range(max_slot):
        slot = probe(s)
        got = call_retry(lambda: getattr(c.functions, fn)(*args_builder).call(state_override={token: {"stateDiff": {Web3.to_hex(slot): Web3.to_hex((12345).to_bytes(32, "big"))}}}))
        if got == 12345:
            return s
    return None


_CACHE: dict = {}


def cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def erc20_override(w3, token, owner, amount):
    """Подмена balanceOf(owner) и allowance(owner, PERMIT2) у токена. None — слоты не найдены."""
    bs = find_slot(w3, token, "balanceOf", [owner], lambda s: keccak(pad(owner) + pad(s)))
    as_ = find_slot(w3, token, "allowance", [owner, PERMIT2], lambda s: keccak(pad(PERMIT2) + keccak(pad(owner) + pad(s))))
    if bs is None or as_ is None:
        return None
    v = Web3.to_hex(int(amount).to_bytes(32, "big"))
    return {Web3.to_hex(keccak(pad(owner) + pad(bs))): v, Web3.to_hex(keccak(pad(PERMIT2) + keccak(pad(owner) + pad(as_)))): v}


def permit2_override(w3, token, owner, spender, amount):
    p2 = w3.eth.contract(address=Web3.to_checksum_address(PERMIT2), abi=P2_ABI)
    packed = (2 ** 160 - 1) | ((int(time.time()) + 86400) << 160)
    for s in range(8):
        slot = keccak(pad(spender) + keccak(pad(token) + keccak(pad(owner) + pad(s))))
        got = call_retry(lambda: p2.functions.allowance(owner, token, spender).call(state_override={PERMIT2: {"stateDiff": {Web3.to_hex(slot): Web3.to_hex(packed.to_bytes(32, "big"))}}}))
        if got is not None and got[0] == 2 ** 160 - 1:
            return {Web3.to_hex(slot): Web3.to_hex(packed.to_bytes(32, "big"))}
    return None


def check(name, ok, extra=""):
    global failed
    print(f"{'PASS' if ok else 'FAIL'}  {name} {extra}")
    failed += 0 if ok else 1


def skip(name, why):
    global skipped
    skipped += 1
    print(f"SKIP  {name} — {why}")


for chain, pool_id, title in POOLS:
    print(f"\n== {chain}: {title}")
    w3 = Web3(Web3.HTTPProvider(SIM_RPC.get(chain, RPC_URLS[chain]), request_kwargs={"timeout": 30}))
    posm = Web3.to_checksum_address(V4_POSITION_MANAGER[chain])
    sv = w3.eth.contract(address=Web3.to_checksum_address(V4_STATE_VIEW[chain]), abi=ABI)
    pm = w3.eth.contract(address=posm, abi=ABI)
    try:
        c0, c1, fee, ts, hooks = pm.functions.poolKeys(bytes.fromhex(pool_id[2:52])).call()
        sqrt_cur, tick, _, _ = sv.functions.getSlot0(bytes.fromhex(pool_id[2:])).call()
    except Exception as e:
        skip(title, f"пул недоступен: {str(e)[:80]}")
        continue
    if c0 == ZERO and c1 == ZERO:
        skip(title, "PositionManager не знает пул (создан не через него) — ключ пула неизвестен")
        continue
    assert Web3.to_hex(compute_pool_id(c0, c1, fee, ts, hooks)).lower() == pool_id.lower(), "poolId не сходится с poolKey"
    dec = lambda a: 18 if a == ZERO else w3.eth.contract(address=a, abi=ABI).functions.decimals().call()
    d0, d1 = dec(c0), dec(c1)
    print(f"   c0={c0[:10]} c1={c1[:10]} fee={fee} spacing={ts} hooks={'нет' if hooks == ZERO else hooks[:10]} tick={tick}")
    if hooks != ZERO:
        skip(title, "у пула есть hooks — вне области бота (hooks=0)")
        continue
    pool_price = (sqrt_cur / Q96) ** 2 * 10 ** d0 / 10 ** d1

    for quote_is_c1 in (True, False):
        quote, asset = (c1, c0) if quote_is_c1 else (c0, c1)
        if quote == ZERO and quote_is_c1:
            continue
        label = f"quote={'currency1' if quote_is_c1 else 'currency0'}{' (native)' if quote == ZERO else ''}"
        p_asset = pool_price if quote_is_c1 else 1 / pool_price          # котировка за актив
        usd_per_quote = 2700.0 if quote == ZERO else 1.0
        levels = [{"lower": p_asset * 0.97, "upper": p_asset * 0.99, "amount": 40.0}, {"lower": p_asset * 0.90, "upper": p_asset * 0.97, "amount": 60.0}]
        plans = plan_ladder(sqrt_cur=sqrt_cur, dec0=d0, dec1=d1, tick_spacing=ts, levels=levels, quote_is_c1=quote_is_c1, usd_per_quote=usd_per_quote)
        bad = [p.error for p in plans if p.error]
        if bad:
            check(f"{label}: план построен", False, str(bad))
            continue
        # для стейбл-пары «цена ≈ 1» диапазон в 1-3% уже почти вырожден по тикам — это нормально, проверяем что план допустим
        deadline = int(time.time()) + 600
        for pl in plans:
            tx = build_level_tx(posm, c0, c1, fee, ts, hooks, pl, FROM, deadline)
            override = {}
            if quote == ZERO:
                override[FROM] = {"balance": hex(10 ** 22)}
            else:
                qc = Web3.to_checksum_address(quote)
                eo = cached(("erc20", chain, qc), lambda: erc20_override(w3, qc, FROM, 10 ** 30))
                po = cached(("p2", chain, qc), lambda: permit2_override(w3, qc, FROM, posm, 10 ** 30))
                if eo is None or po is None:
                    skip(f"{label} уровень {pl.index}", f"не нашла слоты состояния токена {quote[:10]} (balance/allowance/Permit2) — ERC20-путь тут не проверен")
                    break
                override[Web3.to_checksum_address(quote)] = {"stateDiff": eo}
                override[PERMIT2] = {"stateDiff": po}
            call = {"from": FROM, "to": tx["to"], "data": tx["data"], "value": tx["value"]}
            try:
                w3.eth.call(call, "latest", state_override=override)
                check(f"{label}: уровень {pl.index} ticks[{pl.tick_lower},{pl.tick_upper}] принят PositionManager", True, f"value={tx['value']}")
            except Exception as e:
                check(f"{label}: уровень {pl.index} принят PositionManager", False, str(e)[:160])
                continue
            # контроль: без подмены средств тот же вызов должен отклоняться
            try:
                w3.eth.call(call, "latest", state_override={FROM: {"balance": 0}})
                check(f"{label}: уровень {pl.index} без средств ОТКЛОНЯЕТСЯ (контроль)", False, "принят без средств!")
            except Exception:
                check(f"{label}: уровень {pl.index} без средств ОТКЛОНЯЕТСЯ (контроль)", True)
            break        # одного уровня на ориентацию достаточно для проверки формата; остальные — та же формула

print(f"\nfailed={failed} skipped={skipped}")
sys.exit(1 if failed else 0)
