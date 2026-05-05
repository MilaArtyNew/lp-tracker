from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from modules.ladder_builder import build_ladder, format_ladder
from utils.price import get_price_coingecko

router = Router()

QUOTE_ASSETS = ["USDT", "ETH", "USDC"]
MODES = ["aggressive", "optimal", "conservative"]


def kb_choices(options: list[str]) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=o)] for o in options]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


class LadderFSM(StatesGroup):
    token = State()
    quote_asset = State()
    price_mode = State()
    manual_price = State()
    deposit = State()
    drawdown = State()
    risk_mode = State()


@router.message(Command("new_ladder"))
async def cmd_new_ladder(message: Message, state: FSMContext):
    await state.set_state(LadderFSM.token)
    await message.answer(
        "Шаг 1/6 — Введи <b>тикер токена</b> (например: <code>ETH</code>)\n"
        "или контракт (0x...)",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LadderFSM.token)
async def fsm_token(message: Message, state: FSMContext):
    await state.update_data(token=message.text.strip())
    await state.set_state(LadderFSM.quote_asset)
    await message.answer(
        "Шаг 2/6 — Выбери <b>quote asset</b>:",
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
    await state.set_state(LadderFSM.price_mode)
    await message.answer(
        "Шаг 3/6 — Цену брать <b>автоматически</b> или ввести вручную?",
        parse_mode="HTML",
        reply_markup=kb_choices(["auto", "manual"]),
    )


@router.message(LadderFSM.price_mode)
async def fsm_price_mode(message: Message, state: FSMContext):
    mode = message.text.strip().lower()
    if mode not in ("auto", "manual"):
        await message.answer("Выбери: auto или manual")
        return
    await state.update_data(price_mode=mode)

    if mode == "manual":
        await state.set_state(LadderFSM.manual_price)
        await message.answer(
            "Введи текущую цену (например: <code>1.25</code>):",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await state.set_state(LadderFSM.deposit)
        await message.answer(
            "Шаг 4/6 — Введи <b>размер депозита</b> в USD (например: <code>1000</code>):",
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
    await state.update_data(manual_price=price)
    await state.set_state(LadderFSM.deposit)
    await message.answer(
        "Шаг 4/6 — Введи <b>размер депозита</b> в USD:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


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
        "Шаг 5/6 — Drawdown (%) — насколько глубоко строим лестницу:\n"
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
        "Шаг 6/6 — Режим риска:\n"
        "• <b>aggressive</b> — 3–4 уровня\n"
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

    await message.answer("⏳ Считаю...", reply_markup=ReplyKeyboardRemove())

    if data["price_mode"] == "auto":
        price = await get_price_coingecko(token)
        if price is None:
            await state.clear()
            await message.answer(
                "❌ Не удалось получить цену.\n"
                "Попробуй /new_ladder и выбери <b>manual</b>.",
                parse_mode="HTML",
            )
            return
    else:
        price = data["manual_price"]

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
