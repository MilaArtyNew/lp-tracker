"""Команды общей базы: /link (привязка расширения) и /wallet (смена активного кошелька)."""
from aiogram import F, Router
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
