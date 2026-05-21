from dataclasses import dataclass
from config import LADDER_LEVELS, LADDER_WEIGHTS


@dataclass
class LadderLevel:
    index: int
    lower: float
    upper: float
    weight: float
    amount: float


@dataclass
class LadderResult:
    token: str
    pair: str
    current_price: float
    lower_bound: float
    drawdown_pct: int
    deposit: float
    mode: str
    levels: list[LadderLevel]
    fee_tier: str = "0.3%"


def build_ladder(
    token: str,
    quote_asset: str,
    current_price: float,
    deposit: float,
    drawdown_pct: int,
    mode: str = "optimal",
) -> LadderResult:
    lower_bound = current_price * (1 - drawdown_pct / 100)
    n_levels = LADDER_LEVELS[mode]
    weights = LADDER_WEIGHTS[mode]

    step = (current_price - lower_bound) / n_levels

    levels = []
    for i in range(n_levels):
        lvl_lower = lower_bound + i * step
        lvl_upper = lower_bound + (i + 1) * step
        w = weights[i] / 100
        amount = round(deposit * w, 2)
        levels.append(LadderLevel(
            index=i + 1,
            lower=round(lvl_lower, 6),
            upper=round(lvl_upper, 6),
            weight=weights[i],
            amount=amount,
        ))

    # Reverse: show highest range first
    levels = list(reversed(levels))
    for i, lvl in enumerate(levels):
        lvl.index = i + 1

    return LadderResult(
        token=token,
        pair=f"{token}/{quote_asset}",
        current_price=current_price,
        lower_bound=round(lower_bound, 6),
        drawdown_pct=drawdown_pct,
        deposit=deposit,
        mode=mode,
        levels=levels,
    )


def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.0001:
        return f"{p:.6f}"
    return f"{p:.8f}"


def format_ladder(result: LadderResult) -> str:
    lines = [
        f"<b>LP Ladder — {result.pair}</b>",
        f"",
        f"💲 Price:    <code>{_fmt_price(result.current_price)}</code>",
        f"📉 Drawdown: <code>-{result.drawdown_pct}%</code>",
        f"💰 Deposit:  <code>${result.deposit:,.0f}</code>",
        f"📊 Mode:     <code>{result.mode}</code>",
        f"",
    ]

    for lvl in result.levels:
        lines += [
            f"<b>#{lvl.index}</b>  {lvl.weight}%  <b>${lvl.amount:,.2f}</b>",
            f"  lower: <code>{_fmt_price(lvl.lower)}</code>",
            f"  upper: <code>{_fmt_price(lvl.upper)}</code>",
            f"",
        ]

    return "\n".join(lines)
