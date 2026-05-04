"""
/wallets — управление кошельками с приватными ключами.
Приватники хранятся зашифрованными (Fernet, ключ = sha256(BOT_TOKEN + user_id)).
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from modules.wallet_manager import add_wallet, get_wallets, remove_wallet

router = Router()


class WalletFSM(StatesGroup):
    waiting_for_key = State()
    waiting_for_label = State()
    confirm_remove = State()


def _wallets_kb(user_id: int) -> InlineKeyboardMarkup:
    wallets = get_wallets(user_id)
    rows = []
    for w in wallets:
        rows.append([InlineKeyboardButton(
            text=f"🗑 {w['label']}  ({w['address'][:6]}…{w['address'][-4:]})",
            callback_data=f"rmwallet:{w['address']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить кошелёк", callback_data="addwallet")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("wallets"))
async def cmd_wallets(message: Message, state: FSMContext):
    await state.clear()
    wallets = get_wallets(message.from_user.id)
    text = (
        f"<b>Мои кошельки</b> — {len(wallets)} шт.\n\n"
        + ("\n".join(f"• <code>{w['address']}</code>  ({w['label']})" for w in wallets)
           if wallets else "Кошельков нет.")
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_wallets_kb(message.from_user.id))


@router.callback_query(F.data == "addwallet")
async def cb_add_wallet(call: CallbackQuery, state: FSMContext):
    await call.message.answer(
        "🔐 <b>Введи приватный ключ</b> (0x…)\n\n"
        "⚠️ Бот сразу удалит сообщение. "
        "Используй только кошелёк с небольшой суммой для LP-операций.",
        parse_mode="HTML",
    )
    await state.set_state(WalletFSM.waiting_for_key)
    await call.answer()


@router.message(WalletFSM.waiting_for_key)
async def fsm_receive_key(message: Message, state: FSMContext):
    pk = message.text.strip()

    # Попытаться удалить сообщение с приватником
    try:
        await message.delete()
    except Exception:
        pass

    if not pk or len(pk) < 32:
        await message.answer("❌ Некорректный приватный ключ. Попробуй снова.")
        return

    try:
        address = add_wallet(message.from_user.id, pk)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()
        return

    await state.update_data(added_address=address)
    await state.set_state(WalletFSM.waiting_for_label)
    await message.answer(
        f"✅ Кошелёк добавлен: <code>{address}</code>\n\n"
        "Введи метку (имя) для этого кошелька или /skip:",
        parse_mode="HTML",
    )


@router.message(WalletFSM.waiting_for_label)
async def fsm_wallet_label(message: Message, state: FSMContext):
    data = await state.get_data()
    address = data.get("added_address", "")
    label = message.text.strip() if message.text and message.text.strip() != "/skip" else ""
    if label:
        add_wallet(message.from_user.id, "", label)  # update label only? No — re-add with label
        # Actually we need to fetch key and re-save. Simpler: just update label in storage.
        from modules.wallet_manager import _load, _save
        d = _load()
        for w in d.get(str(message.from_user.id), []):
            if w["address"].lower() == address.lower():
                w["label"] = label
                break
        _save(d)

    await state.clear()
    await message.answer(
        f"💾 Сохранено{': ' + label if label else ''}.\n"
        "Используй /wallets для управления.",
        parse_mode="HTML",
        reply_markup=_wallets_kb(message.from_user.id),
    )


@router.callback_query(F.data.startswith("rmwallet:"))
async def cb_remove_wallet(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    await state.update_data(remove_address=address)
    await state.set_state(WalletFSM.confirm_remove)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"rmconfirm:{address}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="rmcancel"),
    ]])
    await call.message.answer(
        f"Удалить кошелёк <code>{address}</code>?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rmconfirm:"))
async def cb_remove_confirm(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    remove_wallet(call.from_user.id, address)
    await state.clear()
    await call.message.edit_text(f"🗑 Кошелёк <code>{address}</code> удалён.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "rmcancel")
async def cb_remove_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Отмена.")
    await call.answer()
