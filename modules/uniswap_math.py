import math

Q96 = 2 ** 96


def tick_to_sqrt_price_x96(tick: int) -> int:
    return int(math.sqrt(1.0001 ** tick) * Q96)


def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    price = (sqrt_price_x96 / Q96) ** 2
    return price * (10 ** decimals0) / (10 ** decimals1)


def get_amounts(
    liquidity: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
    current_tick: int,
) -> tuple[float, float]:
    if liquidity == 0:
        return 0.0, 0.0

    sqrt_lower = tick_to_sqrt_price_x96(tick_lower)
    sqrt_upper = tick_to_sqrt_price_x96(tick_upper)
    sqrt_cur = sqrt_price_x96

    if current_tick < tick_lower:
        amt0 = liquidity * Q96 * (sqrt_upper - sqrt_lower) / (sqrt_lower * sqrt_upper)
        amt1 = 0.0
    elif current_tick >= tick_upper:
        amt0 = 0.0
        amt1 = liquidity * (sqrt_upper - sqrt_lower) / Q96
    else:
        amt0 = liquidity * Q96 * (sqrt_upper - sqrt_cur) / (sqrt_cur * sqrt_upper)
        amt1 = liquidity * (sqrt_cur - sqrt_lower) / Q96

    return amt0, amt1


def position_status(current_tick: int, tick_lower: int, tick_upper: int) -> str:
    if current_tick < tick_lower:
        return "below"
    if current_tick >= tick_upper:
        return "above"
    return "in_range"
