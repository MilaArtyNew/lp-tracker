from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardRemove

from modules.rpc_tracker import fetch_all_chains_rpc
from modules.position_tracker import format_positions
from modules.aggregator import aggregate_by_pair, format_full_report
from data.storage import save_wallet, get_wallet

router = Router()


class TrackFSM(StatesGroup):
    wallet = State()


@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext):
    await state.set_state(TrackFSM.wallet)
    await message.answer(
        "Enter wallet address (0x...):",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(TrackFSM.wallet)
async def fsm_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    if not (wallet.startswith("0x") and len(wallet) == 42):
        await message.answer("Invalid format. Address must be 0x… (42 characters).")
        return

    await state.clear()
    save_wallet(message.from_user.id, wallet)
    await message.answer(
        f"✅ Wallet saved: <code>{wallet}</code>\n\n"
        f"⏳ Scanning all networks (ETH / Arbitrum / Base / BNB)...",
        parse_mode="HTML",
    )
    await _show_report(message, wallet)


@router.message(Command("report"))
async def cmd_report(message: Message):
    wallet = get_wallet(message.from_user.id)
    if not wallet:
        await message.answer("No wallet found. Use /track first.")
        return
    await message.answer(
        f"⏳ Loading positions...\n"
        f"Wallet: <code>{wallet}</code>",
        parse_mode="HTML",
    )
    await _show_report(message, wallet)


@router.message(Command("strategies"))
async def cmd_strategies(message: Message):
    wallet = get_wallet(message.from_user.id)
    if not wallet:
        await message.answer("No wallet found. Use /track first.")
        return
    await message.answer("⏳ Loading positions...")
    await _show_positions(message, wallet)


async def _show_positions(message: Message, wallet: str):
    try:
        positions = await fetch_all_chains_rpc(wallet)
        text = format_positions(positions, wallet)
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")


async def _show_report(message: Message, wallet: str):
    try:
        positions = await fetch_all_chains_rpc(wallet)
        if not positions:
            await message.answer("No active LP positions found on any network.")
            return
        strategies = aggregate_by_pair(positions)
        text = format_full_report(strategies, wallet)
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")
