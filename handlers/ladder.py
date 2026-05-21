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
    rows.append([InlineKeyboardButton(text="✏️ Enter manually", callback_data="ldf:manual")])
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
        "Step 1/7 — Enter the <b>token ticker</b> (e.g. <code>ETH</code>)\n"
        "or contract address (0x...):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LadderFSM.token)
async def fsm_token(message: Message, state: FSMContext):
    await state.update_data(token=message.text.strip())
    await state.set_state(LadderFSM.quote_asset)
    await message.answer(
        "Step 2/7 — Choose a <b>quote asset</b>:",
        parse_mode="HTML",
        reply_markup=kb_choices(QUOTE_ASSETS),
    )


@router.message(LadderFSM.quote_asset)
async def fsm_quote_asset(message: Message, state: FSMContext):
    qa = message.text.strip().upper()
    if qa not in QUOTE_ASSETS:
        await message.answer(f"Please choose from: {', '.join(QUOTE_ASSETS)}")
        return
    await state.update_data(quote_asset=qa)
    await state.set_state(LadderFSM.select_chain)
    await message.answer(
        "Step 3/7 — Which chain is the pool on?",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Select chain:", reply_markup=_chain_kb())


@router.callback_query(LadderFSM.select_chain, F.data.startswith("ldc:"))
async def cb_ladder_chain(call: CallbackQuery, state: FSMContext):
    chain = call.data.split(":", 1)[1]
    await state.update_data(ladder_chain=chain)
    await call.message.answer("Step 4/7 — Select pool fee tier:", reply_markup=_fee_kb())
    await state.set_state(LadderFSM.select_fee)
    await call.answer()


@router.callback_query(LadderFSM.select_fee, F.data.startswith("ldf:"))
async def cb_ladder_fee(call: CallbackQuery, state: FSMContext):
    raw = call.data.split(":", 1)[1]
    if raw == "manual":
        await call.message.answer(
            "Enter fee in percent:\n"
            "0.01 = 0.01%,  0.05 = 0.05%,  0.3 = 0.30%,  1 = 1.00%"
        )
        await state.set_state(LadderFSM.enter_fee_manual)
        await call.answer()
        return
    await call.answer()
    await _apply_ladder_fee(int(raw), state, call.message)


@router.message(LadderFSM.enter_fee_manual)
async def fsm_ladder_fee_manual(message: Message, state: FSMContext):
    try:
        pct = float(message.text.strip().replace("%", "").replace(",", "."))
        fee = round(pct * 10000)
        if fee <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid value. Enter a percentage, e.g.: 0.05 or 1")
        return
    await _apply_ladder_fee(fee, state, message)


async def _apply_ladder_fee(fee: int, state: FSMContext, msg: Message) -> None:
    tick_spacing = TICK_SPACINGS.get(fee, max(1, fee // 50))
    await state.update_data(ladder_fee=fee, ladder_tick_spacing=tick_spacing)

    data = await state.get_data()
    chain = data["ladder_chain"]
    token = data["token"]
    quote = data["quote_asset"]

    await msg.answer("⏳ Fetching price from pool…")
    price = await asyncio.to_thread(get_pool_price, chain, token, quote, fee)

    if price is not None:
        await state.update_data(current_price=price)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Continue", callback_data="ldp:ok"),
            InlineKeyboardButton(text="✏️ Change price", callback_data="ldp:manual"),
        ]])
        await msg.answer(
            f"✅ Pool price: <code>{price:.6g}</code>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await state.set_state(LadderFSM.confirm_price)
    else:
        await msg.answer(
            "⚠️ Could not fetch pool price.\n"
            "Enter the current price manually (e.g. <code>1.25</code>):",
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
        "Enter the current price manually (e.g. <code>1.25</code>):",
        parse_mode="HTML",
    )
    await state.set_state(LadderFSM.manual_price)


async def _ask_deposit(msg: Message, state: FSMContext) -> None:
    await state.set_state(LadderFSM.deposit)
    await msg.answer(
        "Step 5/7 — Enter <b>deposit size</b> in USD (e.g. <code>1000</code>):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LadderFSM.manual_price)
async def fsm_manual_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Enter a number, e.g.: 1.25")
        return
    await state.update_data(current_price=price)
    await _ask_deposit(message, state)


@router.message(LadderFSM.deposit)
async def fsm_deposit(message: Message, state: FSMContext):
    try:
        deposit = float(message.text.strip().replace(",", ".").replace("$", ""))
    except ValueError:
        await message.answer("Enter a number, e.g.: 1000")
        return
    await state.update_data(deposit=deposit)
    await state.set_state(LadderFSM.drawdown)
    await message.answer(
        "Step 6/7 — Drawdown (%) — how deep to build the ladder:\n"
        "Choose or enter manually (10–95):",
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
        await message.answer("Enter a number between 10 and 95 (e.g. 60)")
        return
    await state.update_data(drawdown=drawdown)
    await state.set_state(LadderFSM.risk_mode)
    await message.answer(
        "Step 7/7 — Risk mode:\n"
        "• <b>aggressive</b> — 4 levels\n"
        "• <b>optimal</b> — 6 levels\n"
        "• <b>conservative</b> — 10 levels",
        parse_mode="HTML",
        reply_markup=kb_choices(MODES),
    )


@router.message(LadderFSM.risk_mode)
async def fsm_risk_mode(message: Message, state: FSMContext):
    mode = message.text.strip().lower()
    if mode not in MODES:
        await message.answer(f"Choose from: {', '.join(MODES)}")
        return

    data = await state.get_data()
    token = data["token"]
    quote_asset = data["quote_asset"]
    deposit = data["deposit"]
    drawdown = data["drawdown"]
    price = data["current_price"]

    await message.answer("⏳ Calculating...", reply_markup=ReplyKeyboardRemove())

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
        InlineKeyboardButton(text="🚀 Open positions on chain", callback_data="open_positions"),
    ]])
    await message.answer(format_ladder(result), parse_mode="HTML", reply_markup=open_kb)
