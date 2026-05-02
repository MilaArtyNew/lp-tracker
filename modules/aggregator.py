from dataclasses import dataclass, field
from modules.position_tracker import LPPosition


@dataclass
class Strategy:
    name: str
    positions: list[LPPosition] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return round(sum(p.value_usd for p in self.positions), 2)

    @property
    def total_fees(self) -> float:
        return round(sum(p.fees_usd for p in self.positions), 2)

    @property
    def total_deposited_usd(self) -> float:
        # Approximate: deposited token amounts at current prices
        # Not historical — serves as reference point, not exact PnL
        return round(sum(p._deposited_usd for p in self.positions), 2)

    @property
    def net_pnl(self) -> float:
        return round(self.total_value - self.total_deposited_usd, 2)

    @property
    def net_pnl_with_fees(self) -> float:
        return round(self.total_value + self.total_fees - self.total_deposited_usd, 2)

    @property
    def count_in_range(self) -> int:
        return sum(1 for p in self.positions if p.status == "in_range")

    @property
    def count_below(self) -> int:
        return sum(1 for p in self.positions if p.status == "below")

    @property
    def count_above(self) -> int:
        return sum(1 for p in self.positions if p.status == "above")


def aggregate_by_pair(positions: list[LPPosition]) -> list[Strategy]:
    groups: dict[str, Strategy] = {}
    for pos in positions:
        key = pos.pair
        if key not in groups:
            groups[key] = Strategy(name=key)
        groups[key].positions.append(pos)
    return list(groups.values())


def format_strategy(s: Strategy, idx: int) -> str:
    pnl = s.net_pnl
    pnl_fees = s.net_pnl_with_fees
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_fees_sign = "+" if pnl_fees >= 0 else ""

    lines = [
        f"<b>Стратегия #{idx}: {s.name}</b>",
        f"  Позиций: {len(s.positions)}  "
        f"(🟢{s.count_in_range} 🔴{s.count_below} 🔵{s.count_above})",
        f"  Текущая стоимость: <code>${s.total_value:,.2f}</code>",
        f"  Комиссии собрано:  <code>${s.total_fees:,.2f}</code>",
    ]
    if s.total_deposited_usd > 0:
        lines += [
            f"  Вложено:           <code>${s.total_deposited_usd:,.2f}</code>",
            f"  PnL (без комиссий): <b>{pnl_sign}${pnl:,.2f}</b>",
            f"  PnL (с комиссиями): <b>{pnl_fees_sign}${pnl_fees:,.2f}</b>",
        ]
    return "\n".join(lines)


def format_full_report(strategies: list[Strategy], wallet: str) -> str:
    total_deposited = sum(s.total_deposited_usd for s in strategies)
    total_value = sum(s.total_value for s in strategies)
    total_fees = sum(s.total_fees for s in strategies)
    total_pnl = total_value - total_deposited
    total_pnl_fees = total_value + total_fees - total_deposited
    pnl_pct = (total_pnl / total_deposited * 100) if total_deposited else 0

    sign = "+" if total_pnl >= 0 else ""
    sign_fees = "+" if total_pnl_fees >= 0 else ""

    lines = [
        f"<b>📊 LP Strategy Report</b>",
        f"<code>{wallet[:8]}…{wallet[-4:]}</code>",
        "",
        f"Стратегий:        <b>{len(strategies)}</b>",
        f"Текущая стоимость: <b>${total_value:,.2f}</b>",
        f"Комиссии:         <b>${total_fees:,.2f}</b>",
    ]
    if total_deposited > 0:
        lines += [
            f"Вложено:          <b>${total_deposited:,.2f}</b>",
            f"PnL:              <b>{sign}${total_pnl:,.2f} ({sign}{pnl_pct:.1f}%)</b>",
            f"PnL + комиссии:   <b>{sign_fees}${total_pnl_fees:,.2f}</b>",
        ]
    lines += ["", "─" * 30, ""]

    for i, s in enumerate(strategies, 1):
        lines.append(format_strategy(s, i))
        lines.append("")

    return "\n".join(lines)
