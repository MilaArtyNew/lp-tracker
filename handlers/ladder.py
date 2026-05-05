import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

from modules.chain_config import CHAINS, FEE_TIERS, TICK_SPACINGS, get_pool_price
from modules.ladder_builder import build_ladder, format_ladder

router = Router()

QUOTE_ASSETS = ["USDT", "ETH", "USDC"]
MODES = ["aggressive", "optimal", "conservative"]


def kb_choices(options: list[str]) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=o)] for o in options]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _chain_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c.capitalize(), callback_data=f"ldc:{c}")]
        for c in CHAINS
    ])


def _fee_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"ldf:{fee}")]
        for label, fee in FEE_TIERS.items()
    ]
    rows.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="ldf:manual")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class LadderFSM(StatesGroup):
    token            = State()
    quote_asset      = State()
    select_chain     = State()
    select_fee       = State()
    enter_fee_manual = State()
    confirm_price    = State()
    manual_price     = State()
    deposit          = State()
    drawdown         = State()
    risk_mode        = State()


@router.message(Command("new_ladder"))
async def cmd_new_ladder(message: Message, state: FSMContext):
    await state.set_state(LadderFSM.token)
    await message.answer(
        "Шаг 1/7 — Введи <b>тикер токена</b> (например: <code>ETH</code>)\n"
        "или контракт (0x...):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LadderFSM.token)
async def fsm_token(message: Message, state: FSMContext):
    await state.update_data(token=message.text.strip())
    await state.set_state(LadderFSM.quote_asset)
    await message.answer(
        "Шаг 2/7 — Выбери <b>quote asset</b>:",
        parse_mode="HTML",
        reply_markup=kb_choices(QUOTE_ASSETS),
    )


@router.message(LadderFSM.quote_asset)
async def fsm_quote_asset(message: Message, state: FSMContext):
    qa = message.text.strip().upper()
    if qa not in QUOTE_ASSETS:
        await message.answer(f"Нужно выбрать из: {', '.join(QUOTE_ASSETS)}")
        return
    await state.update_data(quote_asset=qa)
    await state.set_state(LadderFSM.select_chain)
    await message.answer(
        "Шаг 3/7 — На каком чейне пул?",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Выбери чейн:", reply_markup=_chain_kb())


@router.callback_query(LadderFSM.select_chain, F.data.startswith("ldc:"))
async def cb_ladder_chain(call: CallbackQuery, state: FSMContext):
    chain = call.data.split(":", 1)[1]
    await state.update_data(ladder_chain=chain)
    await call.message.answer("Шаг 4/7 — Выбери fee tier пула:", reply_markup=_fee_kb())
    await state.set_state(LadderFSM.select_fee)
    await call.answer()


@router.callback_query(LadderFSM.select_fee, F.data.startswith("ldf:"))
async def cb_ladder_fee(call: CallbackQuery, state: FSMContext):
    raw = call.data.split(":", 1)[1]
    if raw == "manual":
        await call.message.answer(
            "Введи fee tier в базисных пунктах:\n"
            "100 = 0.01%,  500 = 0.05%,  3000 = 0.30%,  10000 = 1.00%"
        )
        await state.set_state(LadderFSM.enter_fee_manual)
        await call.answer()
        return
    await call.answer()
    await _apply_ladder_fee(int(raw), state, call.message)


@router.message(LadderFSM.enter_fee_manual)
async def fsm_ladder_fee_manual(message: Message, state: FSMContext):
    try:
        raw = message.text.strip().replace("%", "").replace(",", ".")
        fee = int(float(raw) * 100) if "." in raw else int(raw)
        if fee <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Некорректное значение. Введи число, например: 500 или 0.05%")
        return
    await _apply_ladder_fee(fee, state, message)


async def _apply_ladder_fee(fee: int, state: FSMContext, msg: Message) -> None:
    tick_spacing = TICK_SPACINGS.get(fee, max(1, fee // 50))
    await state.update_data(ladder_fee=fee, ladder_tick_spacing=tick_spacing)

    data = await state.get_data()
    chain = data["ladder_chain"]
    token = data["token"]
    quote = data["quote_asset"]

    await msg.answer("⏳ Получаю цену из пула…")
    price = await asyncio.to_thread(get_pool_price, chain, token, quote, fee)

    if price is not None:
        await state.update_data(current_price=price)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Продолжить", callback_data="ldp:ok"),
            InlineKeyboardButton(text="✏️ Изменить цену", callback_data="ldp:manual"),
        ]])
        await msg.answer(
            f"✅ Цена из пула: <code>{price:.6g}</code>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await state.set_state(LadderFSM.confirm_price)
    else:
        await msg.answer(
            "⚠️ Не удалось получить цену из пула.\n"
            "Введи текущую цену вручную (например: <code>1.25</code>):",
            parse_mode="HTML",
        )
        await state.set_state(LadderFSM.manual_price)


@router.callback_query(LadderFSM.confirm_price, F.data == "ldp:ok")
async def cb_price_ok(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _ask_deposit(call.message, state)


@router.callback_query(LadderFSM.confirm_price, F.data == "ldp:manual")
async def cb_price_manual(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Введи текущую цену вручную (например: <code>1.25</code>):",
        parse_mode="HTML",
    )
    await state.set_state(LadderFSM.manual_price)


async def _ask_deposit(msg: Message, state: FSMContext) -> None:
    await state.set_state(LadderFSM.deposit)
    await msg.answer(
        "Шаг 5/7 — Введи <b>размер депозита</b> в USD (например: <code>1000</code>):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LadderFSM.manual_price)
async def fsm_manual_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Введи число, например: 1.25")
        return
    await state.update_data(current_price=price)
    await _ask_deposit(message, state)


@router.message(LadderFSM.deposit)
async def fsm_deposit(message: Message, state: FSMContext):
    try:
        deposit = float(message.text.strip().replace(",", ".").replace("$", ""))
    except ValueError:
        await message.answer("Введи число, например: 1000")
        return
    await state.update_data(deposit=deposit)
    await state.set_state(LadderFSM.drawdown)
    await message.answer(
        "Шаг 6/7 — Drawdown (%) — насколько глубоко строим лестницу:\n"
        "Выбери или введи вручную (10–95):",
        parse_mode="HTML",
        reply_markup=kb_choices(["50", "60", "70"]),
    )


@router.message(LadderFSM.drawdown)
async def fsm_drawdown(message: Message, state: FSMContext):
    try:
        drawdown = int(message.text.strip().replace("%", ""))
        if not (10 <= drawdown <= 95):
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 10 до 95 (например: 60)")
        return
    await state.update_data(drawdown=drawdown)
    await state.set_state(LadderFSM.risk_mode)
    await message.answer(
        "Шаг 7/7 — Режим риска:\n"
        "• <b>aggressive</b> — 4 уровня\n"
        "• <b>optimal</b> — 6 уровней\n"
        "• <b>conservative</b> — 10 уровней",
        parse_mode="HTML",
        reply_markup=kb_choices(MODES),
    )


@router.message(LadderFSM.risk_mode)
async def fsm_risk_mode(message: Message, state: FSMContext):
    mode = message.text.strip().lower()
    if mode not in MODES:
        await message.answer(f"Выбери из: {', '.join(MODES)}")
        return

    data = await state.get_data()
    token = data["token"]
    quote_asset = data["quote_asset"]
    deposit = data["deposit"]
    drawdown = data["drawdown"]
    price = data["current_price"]

    await message.answer("⏳ Считаю...", reply_markup=ReplyKeyboardRemove())

    result = build_ladder(
        token=token,
        quote_asset=quote_asset,
        current_price=price,
        deposit=deposit,
        drawdown_pct=drawdown,
        mode=mode,
    )

    ladder_levels = [
        {"lower": lvl.lower, "upper": lvl.upper, "amount": lvl.amount}
        for lvl in result.levels
    ]
    await state.update_data(
        ladder_levels=ladder_levels,
        ladder_token=token,
        ladder_quote=quote_asset,
    )
    await state.set_state(None)

    open_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Открыть позиции на чейне", callback_data="open_positions"),
    ]])
    await message.answer(format_ladder(result), parse_mode="HTML", reply_markup=open_kb)
