"""Ориентированный расчёт лесенки (LP ladder): чистые функции без сети.

Пара — это «актив» (токен, который торгуем) и «котировка» (в чём считаем цену и вносим депозит: ETH, USDT, USDG…).
В Uniswap порядок токенов — по адресу, а не по смыслу: в v4 нативный ETH (0x0) ВСЕГДА currency0, поэтому в пуле
токен/ETH актив — это currency1 и цена пула (token1/token0) обратна цене актива. Уровни лесенки задаются как
«котировка за актив»; здесь они переводятся в цену пула, тики и ликвидность.

bid-лесенка = диапазоны НИЖЕ цены актива, депозит целиком в котировке.
"""
from dataclasses import dataclass

Q96 = 1 << 96
MIN_TICK, MAX_TICK = -887272, 887272

_M = [
    (0x2, 0xfff97272373d413259a46990580e213a), (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
    (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0), (0x10, 0xffcb9843d60f6159c9db58835c926644),
    (0x20, 0xff973b41fa98c081472e6896dfb254c0), (0x40, 0xff2ea16466c96a3843ec78b326b52861),
    (0x80, 0xfe5dee046a99a2a811c461f1969c3053), (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
    (0x200, 0xf987a7253ac413176f2b074cf7815e54), (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
    (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9), (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
    (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5), (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
    (0x8000, 0x31be135f97d08fd981231505542fcfa6), (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
    (0x20000, 0x5d6af8dedb81196699c329225ee604), (0x40000, 0x2216e584f5fa1ea926041bedfe98),
    (0x80000, 0x48a170391f7dc42444e8fa2),
]


def sqrt_at_tick(tick: int) -> int:
    """Точный порт Uniswap TickMath.getSqrtRatioAtTick (без float)."""
    if not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError(f"tick {tick} out of range")
    a = abs(tick)
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if a & 1 else 1 << 128
    for bit, mul in _M:
        if a & bit:
            ratio = (ratio * mul) >> 128
    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio
    return (ratio >> 32) + (0 if ratio & 0xFFFFFFFF == 0 else 1)


def _amount0(sa: int, sb: int, liq: int, up: bool) -> int:
    if sa > sb:
        sa, sb = sb, sa
    num, den = liq * Q96 * (sb - sa), sb * sa
    return -(-num // den) if up else num // den


def _amount1(sa: int, sb: int, liq: int, up: bool) -> int:
    if sa > sb:
        sa, sb = sb, sa
    num = liq * (sb - sa)
    return -(-num // Q96) if up else num // Q96


def amounts_for_liquidity(sqrt_cur: int, sl: int, su: int, liq: int, up: bool = True) -> tuple[int, int]:
    if sqrt_cur <= sl:
        return _amount0(sl, su, liq, up), 0
    if sqrt_cur >= su:
        return 0, _amount1(sl, su, liq, up)
    return _amount0(sqrt_cur, su, liq, up), _amount1(sl, sqrt_cur, liq, up)


def to_pool_price_range(lower: float, upper: float, quote_is_c1: bool) -> tuple[float, float]:
    """Уровень «котировка за актив» -> цена пула token1/token0.
    Котировка = currency1: актив = currency0, цена пула та же. Иначе цена обратна, границы меняются местами."""
    if lower <= 0 or upper <= lower:
        raise ValueError(f"bad level range {lower}..{upper}")
    return (lower, upper) if quote_is_c1 else (1 / upper, 1 / lower)


def liquidity_from_quote_budget(sqrt_cur: int, tick_lower: int, tick_upper: int, budget_raw: int, quote_is_c1: bool) -> int:
    """Ликвидность, которую даёт budget_raw единиц КОТИРОВКИ. Диапазон обязан лежать на стороне котировки
    (bid: ниже цены актива); иначе для него нужен только актив — ValueError, а не молчаливая ошибка."""
    sl, su = sqrt_at_tick(tick_lower), sqrt_at_tick(tick_upper)
    if quote_is_c1:                               # котировка = token1: её часть диапазона — под ценой пула
        if sqrt_cur <= sl:
            raise ValueError("level is above the current price: it needs the asset, not the quote token")
        top = su if sqrt_cur >= su else sqrt_cur
        return budget_raw * Q96 // (top - sl)
    if sqrt_cur >= su:                            # котировка = token0: её часть диапазона — над ценой пула
        raise ValueError("level is above the current price: it needs the asset, not the quote token")
    bot = sl if sqrt_cur <= sl else sqrt_cur
    return budget_raw * su * bot // (Q96 * (su - bot))


@dataclass
class LevelPlan:
    index: int
    tick_lower: int
    tick_upper: int
    liquidity: int
    need0: int
    need1: int
    amount0_max: int
    amount1_max: int
    error: str | None = None


def plan_ladder(*, sqrt_cur: int, dec0: int, dec1: int, tick_spacing: int, levels: list[dict],
                quote_is_c1: bool, usd_per_quote: float, slippage: float = 0.05) -> list[LevelPlan]:
    """levels: [{"lower","upper","amount"}] — цены «котировка за актив», amount в USD.
    Возвращает по плану на уровень; уровень с ошибкой не отправляется, остальные не страдают."""
    import math

    if usd_per_quote <= 0:
        raise ValueError("usd_per_quote must be > 0")
    dec_quote = dec1 if quote_is_c1 else dec0
    plans: list[LevelPlan] = []
    for i, lvl in enumerate(levels, 1):
        try:
            pl, pu = to_pool_price_range(float(lvl["lower"]), float(lvl["upper"]), quote_is_c1)
            tick = lambda p: math.floor(math.log(p * 10 ** dec1 / 10 ** dec0) / math.log(1.0001))
            rnd = lambda t: round(t / tick_spacing) * tick_spacing
            tl, tu = rnd(tick(pl)), rnd(tick(pu))
            if tl >= tu:
                tu = tl + tick_spacing
            budget = int(float(lvl["amount"]) / usd_per_quote * 10 ** dec_quote)
            liq = liquidity_from_quote_budget(sqrt_cur, tl, tu, budget, quote_is_c1)
            if liq <= 0:
                raise ValueError("zero liquidity")
            n0, n1 = amounts_for_liquidity(sqrt_cur, sqrt_at_tick(tl), sqrt_at_tick(tu), liq, up=True)
            mx = lambda n: n + int(n * slippage) + (1 if n else 0)
            plans.append(LevelPlan(i, tl, tu, liq, n0, n1, mx(n0), mx(n1)))
        except Exception as e:
            plans.append(LevelPlan(i, 0, 0, 0, 0, 0, 0, 0, error=str(e)))
    return plans
