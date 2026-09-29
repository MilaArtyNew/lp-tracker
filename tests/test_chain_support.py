"""Офлайн-тесты поддержки Robinhood Chain и вспомогательной логики."""
import pytest
from web3.exceptions import ContractLogicError

from modules import rpc_tracker as rt
from utils.price import _pick_price, WRAPPED_NATIVE

USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"


def pair(base, quote, usd, native, liq):
    return {"baseToken": {"address": base}, "quoteToken": {"address": quote},
            "priceUsd": str(usd), "priceNative": str(native), "liquidity": {"usd": liq}}


def test_price_from_base_role_prefers_most_liquid_pair():
    pairs = [pair(WETH, USDG, 2700, 2700, 10), pair(WETH, USDG, 2714, 2714, 5000)]
    assert _pick_price(pairs, WETH) == 2714.0


def test_price_of_quote_token_is_derived_from_price_native():
    # ROBIN стоит $2.42 и 2.42 USDG => 1 USDG = 2.42/2.42 = $1.00
    pairs = [pair("0xROBIN", USDG, 2.42, 2.42, 900)]
    assert _pick_price(pairs, USDG) == pytest.approx(1.0)
    # USDG подешевел на 1%: 1 ROBIN = 2.42$, но 2.44 USDG => 1 USDG = 0.9918$
    assert _pick_price([pair("0xROBIN", USDG, 2.42, 2.44, 900)], USDG) == pytest.approx(2.42 / 2.44)


def test_price_none_when_token_absent_or_no_price():
    assert _pick_price([pair("0xA", "0xB", 1, 1, 1)], USDG) is None
    assert _pick_price([], USDG) is None
    assert _pick_price([{"baseToken": {"address": USDG}, "quoteToken": {"address": "0xB"}, "liquidity": {"usd": 5}}], USDG) is None


def test_every_supported_chain_has_a_wrapped_native_for_pricing():
    for chain in ("ethereum", "base", "arbitrum", "bnb", "robinhood"):
        assert WRAPPED_NATIVE[chain].startswith("0x")


def test_robinhood_is_registered_everywhere_the_bot_enumerates_chains():
    from modules.chain_config import CHAINS, KNOWN_TOKENS, _STATE_VIEW
    from config import RPC_URLS, ARCHIVE_RPC_URLS
    assert "robinhood" in CHAINS and "robinhood" in KNOWN_TOKENS and "robinhood" in _STATE_VIEW
    assert "robinhood" in RPC_URLS and "robinhood" in ARCHIVE_RPC_URLS
    assert "robinhood" in rt.PROTOCOLS and "robinhood" in rt.V4_POSITION_MANAGER and "robinhood" in rt.V4_STATE_VIEW
    assert rt.V4_POSITION_MANAGER["robinhood"].lower() == "0x58daec3116aae6d93017baaea7749052e8a04fa7"
    # v4 PositionManager совпадает во всех трёх модулях, где он продублирован
    from modules import v4_executor
    assert v4_executor.V4_POSITION_MANAGER["robinhood"] == rt.V4_POSITION_MANAGER["robinhood"]
    assert v4_executor.V4_STATE_VIEW["robinhood"] == rt.V4_STATE_VIEW["robinhood"] == _STATE_VIEW["robinhood"]


class _Fn:
    def __init__(self, effects):
        self.effects, self.calls = list(effects), 0

    def call(self):
        self.calls += 1
        e = self.effects.pop(0)
        if isinstance(e, Exception):
            raise e
        return e


class _Contract:
    def __init__(self, fn):
        self.functions = type("F", (), {"ownerOf": lambda _self, tid: fn})()


def test_owner_or_none_does_not_retry_a_revert(monkeypatch):
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    fn = _Fn([ContractLogicError("execution reverted")])
    assert rt._owner_or_none(_Contract(fn), 1) is None
    assert fn.calls == 1                      # сожжённый NFT — ответ, а не повод ждать backoff


def test_owner_or_none_retries_transport_errors_then_succeeds(monkeypatch):
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    fn = _Fn([ConnectionError("429"), ConnectionError("429"), "0xOWNER"])
    assert rt._owner_or_none(_Contract(fn), 1) == "0xOWNER"
    assert fn.calls == 3


def test_owner_or_none_gives_up_after_three_transport_errors(monkeypatch):
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    fn = _Fn([ConnectionError("x")] * 3)
    assert rt._owner_or_none(_Contract(fn), 1) is None
    assert fn.calls == 3
