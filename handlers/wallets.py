"""
/wallets — wallet management with private keys.
Keys are stored encrypted (Fernet, key = sha256(BOT_TOKEN + user_id)).
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
    rows.append([InlineKeyboardButton(text="➕ Add wallet", callback_data="addwallet")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("wallets"))
async def cmd_wallets(message: Message, state: FSMContext):
    await state.clear()
    wallets = get_wallets(message.from_user.id)
    text = (
        f"<b>My wallets</b> — {len(wallets)} total\n\n"
        + ("\n".join(f"• <code>{w['address']}</code>  ({w['label']})" for w in wallets)
           if wallets else "No wallets saved.")
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_wallets_kb(message.from_user.id))


@router.callback_query(F.data == "addwallet")
async def cb_add_wallet(call: CallbackQuery, state: FSMContext):
    await call.message.answer(
        "🔐 <b>Enter your private key</b> (0x…)\n\n"
        "⚠️ The bot will delete your message immediately. "
        "Use only a wallet with a small amount for LP operations.",
        parse_mode="HTML",
    )
    await state.set_state(WalletFSM.waiting_for_key)
    await call.answer()


@router.message(WalletFSM.waiting_for_key)
async def fsm_receive_key(message: Message, state: FSMContext):
    pk = message.text.strip()

    try:
        await message.delete()
    except Exception:
        pass

    if not pk or len(pk) < 32:
        await message.answer("❌ Invalid private key. Please try again.")
        return

    try:
        address = add_wallet(message.from_user.id, pk)
    except Exception as e:
        await message.answer(f"❌ Error: {e}")
        await state.clear()
        return

    await state.update_data(added_address=address)
    await state.set_state(WalletFSM.waiting_for_label)
    await message.answer(
        f"✅ Wallet added: <code>{address}</code>\n\n"
        "Enter a label for this wallet or /skip:",
        parse_mode="HTML",
    )


@router.message(WalletFSM.waiting_for_label)
async def fsm_wallet_label(message: Message, state: FSMContext):
    data = await state.get_data()
    address = data.get("added_address", "")
    label = message.text.strip() if message.text and message.text.strip() != "/skip" else ""
    if label:
        from modules.wallet_manager import _load, _save
        d = _load()
        for w in d.get(str(message.from_user.id), []):
            if w["address"].lower() == address.lower():
                w["label"] = label
                break
        _save(d)

    await state.clear()
    await message.answer(
        f"💾 Saved{': ' + label if label else ''}.\n"
        "Use /wallets to manage your wallets.",
        parse_mode="HTML",
        reply_markup=_wallets_kb(message.from_user.id),
    )


@router.callback_query(F.data.startswith("rmwallet:"))
async def cb_remove_wallet(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    await state.update_data(remove_address=address)
    await state.set_state(WalletFSM.confirm_remove)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes, remove", callback_data=f"rmconfirm:{address}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="rmcancel"),
    ]])
    await call.message.answer(
        f"Remove wallet <code>{address}</code>?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rmconfirm:"))
async def cb_remove_confirm(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    remove_wallet(call.from_user.id, address)
    await state.clear()
    await call.message.edit_text(f"🗑 Wallet <code>{address}</code> removed.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "rmcancel")
async def cb_remove_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Cancelled.")
    await call.answer()
