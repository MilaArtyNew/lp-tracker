from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

HELP_TEXT = """
<b>LP Tracker — Uniswap v3 Assistant</b>

<b>Команды:</b>
/new_ladder — построить лестницу LP диапазонов
/track — добавить кошелёк для трекинга позиций
/report — отчёт по всем LP позициям
/strategies — список стратегий
/help — справка
"""


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f"Привет! Я LP Tracker для Uniswap v3.\n\n{HELP_TEXT}",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML")
