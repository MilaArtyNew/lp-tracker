"""Ориентация лесенки: котировка может быть currency0 (нативный ETH всегда им и является) или currency1."""
import pytest
from eth_abi import decode
from web3 import Web3

from modules.ladder_plan import (Q96, MIN_TICK, MAX_TICK, sqrt_at_tick, plan_ladder, to_pool_price_range,
                                 amounts_for_liquidity)
from modules.v4_executor import build_level_tx, MINT_POSITION, SETTLE_PAIR, SWEEP

ZERO = "0x" + "0" * 40
TOKEN = "0x" + "ab" * 20
USDT = "0x" + "cd" * 20


def sqrt_price(p_human: float, dec0: int, dec1: int) -> int:
    """sqrtPriceX96 для цены пула token1/token0 (человеческие единицы)."""
    return int((p_human * 10 ** dec1 / 10 ** dec0) ** 0.5 * Q96)


def test_tickmath_known_values():
    assert sqrt_at_tick(0) == Q96
    assert sqrt_at_tick(MIN_TICK) == 4295128739
    assert sqrt_at_tick(MAX_TICK) == 1461446703485210103287273052203988822378723970342


def test_level_conversion_is_inverted_when_quote_is_currency0():
    assert to_pool_price_range(0.9, 1.0, quote_is_c1=True) == (0.9, 1.0)
    lo, hi = to_pool_price_range(0.0004, 0.0005, quote_is_c1=False)
    assert (lo, hi) == pytest.approx((2000.0, 2500.0)) and lo < hi
    with pytest.raises(ValueError):
        to_pool_price_range(1.0, 0.9, True)


LEVELS = [{"lower": 0.90, "upper": 0.95, "amount": 400.0}, {"lower": 0.80, "upper": 0.90, "amount": 600.0}]


def test_quote_is_currency1_token_usdt_like():
    """TOKEN(18)/USDT(6): актив = currency0, цена пула = USDT за токен. Bid-лесенка — ниже цены, депозит только USDT."""
    dec0, dec1, price = 18, 6, 1.0
    cur = sqrt_price(price, dec0, dec1)
    plans = plan_ladder(sqrt_cur=cur, dec0=dec0, dec1=dec1, tick_spacing=60, levels=LEVELS, quote_is_c1=True, usd_per_quote=1.0)
    assert all(p.error is None for p in plans)
    assert all(p.need0 == 0 and p.need1 > 0 for p in plans)              # актив не нужен
    spent = sum(p.need1 for p in plans) / 10 ** dec1
    assert 999.0 < spent <= 1000.01                                       # весь бюджет $1000 (округление вниз по ликвидности)
    assert all(p.tick_upper <= sqrt_tick(cur) + 60 for p in plans)


def sqrt_tick(sqrt: int) -> int:
    import math
    return math.floor(math.log((sqrt / Q96) ** 2) / math.log(1.0001))


def test_quote_is_currency0_token_eth_like():
    """ETH(c0, 18)/TOKEN(c1, 18): цена пула = токенов за ETH. Актив — TOKEN, котировка — ETH.
    Уровни «ETH за токен» ниже цены актива => цена пула ВЫШЕ текущей; депозит только ETH (currency0)."""
    dec0 = dec1 = 18
    pool_price = 2000.0                        # 1 ETH = 2000 TOKEN => TOKEN = 0.0005 ETH
    cur = sqrt_price(pool_price, dec0, dec1)
    levels = [{"lower": 0.00045, "upper": 0.000475, "amount": 2700.0}, {"lower": 0.0004, "upper": 0.00045, "amount": 2700.0}]
    plans = plan_ladder(sqrt_cur=cur, dec0=dec0, dec1=dec1, tick_spacing=60, levels=levels, quote_is_c1=False, usd_per_quote=2700.0)
    assert all(p.error is None for p in plans), [p.error for p in plans]
    assert all(p.need1 == 0 and p.need0 > 0 for p in plans)              # только ETH
    assert all(p.tick_lower >= sqrt_tick(cur) - 60 for p in plans)       # диапазон в пуле выше текущей цены
    eth = sum(p.need0 for p in plans) / 1e18
    assert 1.99 < eth <= 2.0001                                          # $5400 / $2700 = 2 ETH


def test_wrong_side_level_is_rejected_others_survive():
    cur = sqrt_price(1.0, 18, 6)
    levels = [{"lower": 1.05, "upper": 1.10, "amount": 100.0}, {"lower": 0.90, "upper": 0.95, "amount": 100.0}]
    plans = plan_ladder(sqrt_cur=cur, dec0=18, dec1=6, tick_spacing=60, levels=levels, quote_is_c1=True, usd_per_quote=1.0)
    assert plans[0].error and "asset" in plans[0].error
    assert plans[1].error is None and plans[1].liquidity > 0


def test_mirrored_pools_give_the_same_economic_deposit():
    """Один и тот же bid при обеих ориентациях пула тратит одинаковую сумму котировки."""
    lv = [{"lower": 0.90, "upper": 0.95, "amount": 500.0}]
    a = plan_ladder(sqrt_cur=sqrt_price(1.0, 18, 18), dec0=18, dec1=18, tick_spacing=10, levels=lv, quote_is_c1=True, usd_per_quote=1.0)[0]
    b = plan_ladder(sqrt_cur=sqrt_price(1.0, 18, 18), dec0=18, dec1=18, tick_spacing=10, levels=lv, quote_is_c1=False, usd_per_quote=1.0)[0]
    assert a.error is None and b.error is None
    assert a.need1 == pytest.approx(500e18, rel=2e-3) and b.need0 == pytest.approx(500e18, rel=2e-3)
    assert a.need0 == 0 and b.need1 == 0


def test_straddling_top_level_needs_both_tokens_but_quote_part_matches_budget():
    cur = sqrt_price(1.0, 18, 18)
    lv = [{"lower": 0.97, "upper": 1.03, "amount": 300.0}]
    p = plan_ladder(sqrt_cur=cur, dec0=18, dec1=18, tick_spacing=10, levels=lv, quote_is_c1=True, usd_per_quote=1.0)[0]
    assert p.error is None and p.need0 > 0 and p.need1 == pytest.approx(300e18, rel=2e-3)


def _decode_actions(tx):
    unlock = decode(["bytes", "uint256"], bytes.fromhex(tx["data"][10:]))[0]
    actions, params = decode(["bytes", "bytes[]"], unlock)
    return list(actions), params


def test_native_currency0_sends_value_and_sweeps_the_remainder():
    cur = sqrt_price(2000.0, 18, 18)
    plan = plan_ladder(sqrt_cur=cur, dec0=18, dec1=18, tick_spacing=60, quote_is_c1=False, usd_per_quote=2700.0,
                       levels=[{"lower": 0.00045, "upper": 0.000475, "amount": 1000.0}])[0]
    tx = build_level_tx("0x" + "11" * 20, ZERO, TOKEN, 3000, 60, ZERO, plan, "0x" + "22" * 20, 999)
    actions, params = _decode_actions(tx)
    assert actions == [MINT_POSITION, SETTLE_PAIR, SWEEP]
    assert tx["value"] == plan.amount0_max > 0
    sweep_to = decode(["address", "address"], params[2])
    assert sweep_to[0] == ZERO and sweep_to[1].lower() == "0x" + "22" * 20


def test_erc20_pair_sends_no_value_and_no_sweep():
    plan = plan_ladder(sqrt_cur=sqrt_price(1.0, 18, 6), dec0=18, dec1=6, tick_spacing=60, levels=LEVELS[:1],
                       quote_is_c1=True, usd_per_quote=1.0)[0]
    tx = build_level_tx("0x" + "11" * 20, TOKEN, USDT, 500, 60, ZERO, plan, "0x" + "22" * 20, 999)
    actions, _ = _decode_actions(tx)
    assert actions == [MINT_POSITION, SETTLE_PAIR] and tx["value"] == 0
