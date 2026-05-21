from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

HELP_TEXT = """
<b>LP Tracker — Uniswap v3/v4 Assistant</b>

<b>Commands:</b>
/new_ladder — build an LP range ladder
/track — add a wallet to track positions
/report — full report on all LP positions
/strategies — list positions by wallet
/wallets — manage wallets (add / remove)
/help — show this help
"""


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f"Hi! I'm an LP Tracker for Uniswap v3/v4.\n\n{HELP_TEXT}",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML")
