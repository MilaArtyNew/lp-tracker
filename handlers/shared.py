"""Команды общей базы: /link (привязка расширения) и /wallet (смена активного кошелька)."""
import html

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db import pool, repo

router = Router()


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


@router.message(Command("link"))
async def cmd_link(message: Message):
    if not pool.enabled():
        await message.answer("Shared database is not configured.")
        return
    uid = await repo.get_or_create_user(message.from_user.id)
    code = await repo.create_link_code(uid)
    await message.answer(
        f"Your extension link code:\n<code>{code}</code>\n\n"
        "Enter it in the extension within 10 minutes. The code works once.",
        parse_mode="HTML",
    )


async def _render(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    wallets = await repo.list_wallets(user_id)
    if not wallets:
        return "No wallets yet. Use /track to add one.", None
    lines = ["<b>Active wallet is marked ✅. Tap to switch:</b>"]
    rows = []
    for w in wallets:
        mark = "✅ " if w["is_active"] else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{w['label'] or _short(w['address'])}",
            callback_data=f"wsw:{w['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("wallet"))
async def cmd_wallet(message: Message):
    if not pool.enabled():
        await message.answer("Shared database is not configured.")
        return
    uid = await repo.get_or_create_user(message.from_user.id)
    text, kb = await _render(uid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("wsw:"))
async def cb_switch(query: CallbackQuery):
    uid = await repo.get_or_create_user(query.from_user.id)
    ok = await repo.activate_wallet(uid, int(query.data.split(":")[1]))
    if not ok:
        await query.message.edit_text("Wallet not found.")
        await query.answer()
        return
    w = await repo.active_main(uid)
    text, kb = await _render(uid)
    await query.message.edit_text(
        f"{text}\n\nActive wallet switched to <code>{w['address']}</code>. "
        "Positions will refresh within 2 minutes.",
        reply_markup=kb, parse_mode="HTML")
    await query.answer()


async def deliver_alerts(bot: Bot, user_id: int, events: list[dict]) -> None:
    tg_id = await repo.tg_id_of(user_id)
    if not tg_id:
        return
    lines = []
    for e in events:
        p = e["position"]
        pair = html.escape(f"{p.token0_symbol}/{p.token1_symbol}")          # символы приходят из сети: экранируем
        head = f"{pair} · {html.escape(p.chain)} {html.escape(p.protocol)} #{html.escape(str(p.token_id))}"
        if e["kind"] == "enter":
            lines.append(f"🟢 <b>{head}</b> entered range {p.price_lower:g}–{p.price_upper:g} (price {p.current_price:g})")
        else:
            side = "below" if p.status == "below" else "above"
            lines.append(f"🔴 <b>{head}</b> left range, price is {side} it ({p.current_price:g}; range {p.price_lower:g}–{p.price_upper:g})")
    await bot.send_message(tg_id, "\n".join(lines), parse_mode="HTML")


def _alerts_kb(cfg: dict) -> InlineKeyboardMarkup:
    def btn(field: str, label: str):
        on = cfg[field]
        return InlineKeyboardButton(text=f"{'✅' if on else '⬜'} {label}", callback_data=f"alt:{field}:{0 if on else 1}")
    return InlineKeyboardMarkup(inline_keyboard=[[btn("range_enter", "Entered range")], [btn("range_exit", "Left range")]])


@router.message(Command("alerts"))
async def cmd_alerts(message: Message):
    if not pool.enabled():
        await message.answer("Shared database is not configured.")
        return
    uid = await repo.get_or_create_user(message.from_user.id)
    await message.answer("Range alerts for the active wallet. Tap to toggle:",
                         reply_markup=_alerts_kb(await repo.get_alert_settings(uid)))


@router.callback_query(F.data.startswith("alt:"))
async def cb_alerts(query: CallbackQuery):
    _, field, val = query.data.split(":")
    uid = await repo.get_or_create_user(query.from_user.id)
    await repo.set_alert(uid, field, val == "1")
    await query.message.edit_text("Range alerts for the active wallet. Tap to toggle:",
                                  reply_markup=_alerts_kb(await repo.get_alert_settings(uid)))
    await query.answer("Saved")
