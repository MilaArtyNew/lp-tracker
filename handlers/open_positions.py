"""
FSM для открытия LP позиций с лесенки.
Запускается кнопкой 'Открыть позиции' из ladder.py.
"""
import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from modules.wallet_manager import get_account, get_wallets, add_wallet

router = Router()

CHAINS = ["base", "ethereum", "arbitrum", "bnb"]
FEE_TIERS = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}
TICK_SPACINGS = {100: 1, 500: 10, 3000: 60, 10000: 200}
ZERO_ADDR = "0x0000000000000000000000000000000000000000"


class OpenPosFSM(StatesGroup):
    select_wallet = State()
    enter_key = State()
    select_chain = State()
    enter_token0 = State()
    enter_token1 = State()
    select_fee = State()
    confirm = State()


def _wallet_kb(user_id: int) -> InlineKeyboardMarkup:
    wallets = get_wallets(user_id)
    rows = [[InlineKeyboardButton(
        text=f"{w['label']}  ({w['address'][:6]}…{w['address'][-4:]})",
        callback_data=f"opw:{w['address']}",
    )] for w in wallets]
    rows.append([InlineKeyboardButton(text="🔑 Ввести ключ сейчас", callback_data="opw:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chain_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c.capitalize(), callback_data=f"opc:{c}")]
        for c in CHAINS
    ])


def _fee_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"opf:{fee}")]
        for label, fee in FEE_TIERS.items()
    ])


# ── Entry point (called from ladder.py via callback) ──────────────────────────

@router.callback_query(F.data == "open_positions")
async def cb_open_positions(call: CallbackQuery, state: FSMContext):
    # ladder data must be stored in FSM by ladder.py before this button is shown
    data = await state.get_data()
    if "ladder_levels" not in data:
        await call.answer("Сначала построй лесенку /new_ladder", show_alert=True)
        return

    wallets = get_wallets(call.from_user.id)
    if wallets:
        await call.message.answer("Выбери кошелёк:", reply_markup=_wallet_kb(call.from_user.id))
        await state.set_state(OpenPosFSM.select_wallet)
    else:
        await call.message.answer(
            "🔐 Нет сохранённых кошельков. Введи приватный ключ (0x…):\n\n"
            "⚠️ Сообщение будет удалено сразу."
        )
        await state.set_state(OpenPosFSM.enter_key)
    await call.answer()


# ── Wallet selection ──────────────────────────────────────────────────────────

@router.callback_query(OpenPosFSM.select_wallet, F.data.startswith("opw:"))
async def cb_wallet_selected(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    if address == "new":
        await call.message.answer(
            "🔐 Введи приватный ключ (0x…):\n\n⚠️ Сообщение будет удалено сразу."
        )
        await state.set_state(OpenPosFSM.enter_key)
    else:
        await state.update_data(wallet_address=address)
        await call.message.answer("На каком чейне открываем?", reply_markup=_chain_kb())
        await state.set_state(OpenPosFSM.select_chain)
    await call.answer()


@router.message(OpenPosFSM.enter_key)
async def fsm_enter_key(message: Message, state: FSMContext):
    pk = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        address = add_wallet(message.from_user.id, pk)
        await state.update_data(wallet_address=address)
        await message.answer(
            f"✅ Кошелёк добавлен: <code>{address}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка ключа: {e}")
        return
    await message.answer("На каком чейне открываем?", reply_markup=_chain_kb())
    await state.set_state(OpenPosFSM.select_chain)


# ── Chain ─────────────────────────────────────────────────────────────────────

@router.callback_query(OpenPosFSM.select_chain, F.data.startswith("opc:"))
async def cb_chain_selected(call: CallbackQuery, state: FSMContext):
    chain = call.data.split(":", 1)[1]
    await state.update_data(chain=chain)
    await call.message.answer(
        "Введи адрес <b>token0</b> (меньший по адресу).\n"
        "Например USDC на Base: <code>0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913</code>",
        parse_mode="HTML",
    )
    await state.set_state(OpenPosFSM.enter_token0)
    await call.answer()


@router.message(OpenPosFSM.enter_token0)
async def fsm_token0(message: Message, state: FSMContext):
    addr = message.text.strip()
    if not addr.startswith("0x") or len(addr) != 42:
        await message.answer("❌ Некорректный адрес. Введи снова:")
        return
    await state.update_data(currency0=addr)
    await message.answer("Введи адрес <b>token1</b>:", parse_mode="HTML")
    await state.set_state(OpenPosFSM.enter_token1)


@router.message(OpenPosFSM.enter_token1)
async def fsm_token1(message: Message, state: FSMContext):
    addr = message.text.strip()
    if not addr.startswith("0x") or len(addr) != 42:
        await message.answer("❌ Некорректный адрес. Введи снова:")
        return
    await state.update_data(currency1=addr)
    await message.answer("Выбери <b>fee tier</b> пула:", parse_mode="HTML", reply_markup=_fee_kb())
    await state.set_state(OpenPosFSM.select_fee)


@router.callback_query(OpenPosFSM.select_fee, F.data.startswith("opf:"))
async def cb_fee_selected(call: CallbackQuery, state: FSMContext):
    fee = int(call.data.split(":", 1)[1])
    tick_spacing = TICK_SPACINGS[fee]
    await state.update_data(fee=fee, tick_spacing=tick_spacing)

    data = await state.get_data()
    levels = data["ladder_levels"]
    wallet = data["wallet_address"]
    chain = data["chain"]
    c0 = data["currency0"]
    c1 = data["currency1"]

    confirm_text = (
        f"<b>Подтверди открытие позиций</b>\n\n"
        f"Кошелёк: <code>{wallet[:8]}…{wallet[-4:]}</code>\n"
        f"Чейн: {chain}\n"
        f"Пул: {c0[:8]}… / {c1[:8]}…\n"
        f"Fee: {fee/10000:.2f}%\n\n"
        f"Уровней: {len(levels)}\n"
        + "\n".join(
            f"  #{i+1}: ${lvl['amount']:,.2f}  [{lvl['lower']:.4f} – {lvl['upper']:.4f}]"
            for i, lvl in enumerate(levels)
        )
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Открыть все", callback_data="opconfirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="opcancel"),
    ]])
    await call.message.answer(confirm_text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(OpenPosFSM.confirm)
    await call.answer()


# ── Confirm & execute ─────────────────────────────────────────────────────────

@router.callback_query(OpenPosFSM.confirm, F.data == "opconfirm")
async def cb_confirm_open(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await call.answer()

    status_msg = await call.message.answer("🚀 Начинаю открытие позиций…")

    from web3 import Web3
    from modules.v4_executor import open_ladder_positions
    from modules.rpc_tracker import ERC20_ABI

    chain = data["chain"]
    currency0 = data["currency0"]
    currency1 = data["currency1"]
    fee = data["fee"]
    tick_spacing = data["tick_spacing"]
    wallet_address = data["wallet_address"]
    levels = data["ladder_levels"]

    try:
        account = get_account(call.from_user.id, wallet_address)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка кошелька: {e}")
        return

    # Fetch decimals
    from config import RPC_URLS
    w3 = Web3(Web3.HTTPProvider(RPC_URLS[chain]))
    try:
        t0 = w3.eth.contract(address=Web3.to_checksum_address(currency0), abi=ERC20_ABI)
        t1 = w3.eth.contract(address=Web3.to_checksum_address(currency1), abi=ERC20_ABI)
        dec0 = t0.functions.decimals().call()
        dec1 = t1.functions.decimals().call()
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка получения decimals: {e}")
        return

    log_lines = []

    def progress(msg: str):
        log_lines.append(msg)
        asyncio.get_event_loop().call_soon_threadsafe(
            asyncio.ensure_future,
            status_msg.edit_text("\n".join(log_lines[-15:]), parse_mode="Markdown"),
        )

    try:
        results = await asyncio.to_thread(
            open_ladder_positions,
            chain, account,
            currency0, currency1,
            fee, tick_spacing,
            ZERO_ADDR,  # hooks = zero address (no hooks)
            dec0, dec1,
            levels,
            0.05,
            progress,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Критическая ошибка: {e}")
        return

    ok = sum(1 for r in results if r["error"] is None)
    fail = len(results) - ok
    summary = (
        f"<b>Готово!</b> ✅{ok} / ❌{fail}\n\n"
        + "\n".join(
            f"#{r['level']}: ✅ <code>{r['tx'][:10]}…</code>" if not r["error"]
            else f"#{r['level']}: ❌ {r['error']}"
            for r in results
        )
    )
    await call.message.answer(summary, parse_mode="HTML")


@router.callback_query(OpenPosFSM.confirm, F.data == "opcancel")
async def cb_cancel_open(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Отменено.")
    await call.answer()


ZERO_ADDR = "0x0000000000000000000000000000000000000000"
