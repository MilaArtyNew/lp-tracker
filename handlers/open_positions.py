"""
FSM for opening LP positions from a ladder.
Triggered by the "Open positions" button in ladder.py.
Tokens are already known from the ladder data — only fee tier (+ chain) is asked.
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

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# Known quote token addresses by chain
KNOWN_TOKENS: dict[str, dict[str, str]] = {
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "USDT": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
        "ETH":  ZERO_ADDR,
        "WETH": "0x4200000000000000000000000000000000000006",
        "DAI":  "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "ETH":  ZERO_ADDR,
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "DAI":  "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "arbitrum": {
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "ETH":  ZERO_ADDR,
        "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "DAI":  "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    "bnb": {
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "ETH":  ZERO_ADDR,
        "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "DAI":  "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",
    },
}

FEE_TIERS = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}
TICK_SPACINGS = {100: 1, 500: 10, 3000: 60, 10000: 200}
CHAINS = ["base", "ethereum", "arbitrum", "bnb"]


def _fee_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"opf:{fee}")]
        for label, fee in FEE_TIERS.items()
    ]
    rows.append([InlineKeyboardButton(text="✏️ Enter manually", callback_data="opf:manual")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chain_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c.capitalize(), callback_data=f"opc:{c}")]
        for c in CHAINS
    ])


def _wallet_kb(user_id: int) -> InlineKeyboardMarkup:
    wallets = get_wallets(user_id)
    rows = [[InlineKeyboardButton(
        text=f"{w['label']}  ({w['address'][:6]}…{w['address'][-4:]})",
        callback_data=f"opw:{w['address']}",
    )] for w in wallets]
    rows.append([InlineKeyboardButton(text="🔑 Enter new key", callback_data="opw:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class OpenPosFSM(StatesGroup):
    select_wallet    = State()
    enter_key        = State()
    select_chain     = State()
    select_fee       = State()
    enter_fee_manual = State()
    enter_token_addr = State()


# ── Entry point ──────────────────────────────────────────────────────────────

async def _after_wallet(msg, state: FSMContext) -> None:
    """After wallet is confirmed: skip chain/fee steps if already set by ladder."""
    data = await state.get_data()
    if "ladder_chain" in data and "ladder_fee" in data:
        chain = data["ladder_chain"]
        fee = data["ladder_fee"]
        tick_spacing = data.get("ladder_tick_spacing", TICK_SPACINGS.get(fee, max(1, fee // 50)))
        await state.update_data(chain=chain, fee=fee, tick_spacing=tick_spacing)

        token_raw = data.get("ladder_token", "")
        quote_raw = data.get("ladder_quote", "USDC")
        token_addr = _resolve_addr(token_raw, chain)
        quote_addr = _resolve_addr(quote_raw, chain)

        if token_addr is None:
            await state.update_data(pending_quote_addr=quote_addr)
            await msg.answer(
                f"Enter the contract address for <b>{token_raw}</b> on {chain}:",
                parse_mode="HTML",
            )
            await state.set_state(OpenPosFSM.enter_token_addr)
            return

        await _proceed_to_confirm(msg, state, token_addr, quote_addr)
    else:
        await msg.answer("Which chain to open on?", reply_markup=_chain_kb())
        await state.set_state(OpenPosFSM.select_chain)


@router.callback_query(F.data == "open_positions")
async def cb_open_positions(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "ladder_levels" not in data:
        await call.answer("Build a ladder first with /new_ladder", show_alert=True)
        return

    wallets = get_wallets(call.from_user.id)
    if len(wallets) == 0:
        await call.message.answer(
            "🔐 No saved wallets.\nEnter your private key (0x…):\n\n"
            "⚠️ The message will be deleted immediately."
        )
        await state.set_state(OpenPosFSM.enter_key)
    elif len(wallets) == 1:
        await state.update_data(wallet_address=wallets[0]["address"])
        await _after_wallet(call.message, state)
    else:
        await call.message.answer("Select a wallet:", reply_markup=_wallet_kb(call.from_user.id))
        await state.set_state(OpenPosFSM.select_wallet)
    await call.answer()


# ── Wallet ────────────────────────────────────────────────────────────────────

@router.callback_query(OpenPosFSM.select_wallet, F.data.startswith("opw:"))
async def cb_wallet_selected(call: CallbackQuery, state: FSMContext):
    address = call.data.split(":", 1)[1]
    if address == "new":
        await call.message.answer(
            "🔐 Enter your private key (0x…):\n\n⚠️ The message will be deleted immediately."
        )
        await state.set_state(OpenPosFSM.enter_key)
    else:
        await state.update_data(wallet_address=address)
        await _after_wallet(call.message, state)
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
        await message.answer(f"✅ Wallet: <code>{address}</code>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Key error: {e}")
        return
    await _after_wallet(message, state)


# ── Chain → Fee tier ──────────────────────────────────────────────────────────

@router.callback_query(OpenPosFSM.select_chain, F.data.startswith("opc:"))
async def cb_chain_selected(call: CallbackQuery, state: FSMContext):
    chain = call.data.split(":", 1)[1]
    await state.update_data(chain=chain)
    await call.message.answer("Select pool fee tier:", reply_markup=_fee_kb())
    await state.set_state(OpenPosFSM.select_fee)
    await call.answer()


async def _apply_fee(fee: int, state: FSMContext, msg):
    tick_spacing = TICK_SPACINGS.get(fee, max(1, fee // 50))
    await state.update_data(fee=fee, tick_spacing=tick_spacing)

    data = await state.get_data()
    chain = data["chain"]
    token_raw = data.get("ladder_token", "")
    quote_raw = data.get("ladder_quote", "USDC")

    token_addr = _resolve_addr(token_raw, chain)
    quote_addr = _resolve_addr(quote_raw, chain)

    if token_addr is None:
        await state.update_data(pending_quote_addr=quote_addr)
        await msg.answer(
            f"Enter the contract address for <b>{token_raw}</b> on {chain}:",
            parse_mode="HTML",
        )
        await state.set_state(OpenPosFSM.enter_token_addr)
        return

    await _proceed_to_confirm(msg, state, token_addr, quote_addr)


@router.callback_query(OpenPosFSM.select_fee, F.data.startswith("opf:"))
async def cb_fee_selected(call: CallbackQuery, state: FSMContext):
    raw = call.data.split(":", 1)[1]
    if raw == "manual":
        await call.message.answer(
            "Enter fee in percent:\n"
            "0.01 = 0.01%,  0.05 = 0.05%,  0.3 = 0.30%,  1 = 1.00%"
        )
        await state.set_state(OpenPosFSM.enter_fee_manual)
        await call.answer()
        return

    fee = int(raw)
    await call.answer()
    await _apply_fee(fee, state, call.message)


@router.message(OpenPosFSM.enter_fee_manual)
async def fsm_enter_fee_manual(message: Message, state: FSMContext):
    try:
        pct = float(message.text.strip().replace("%", "").replace(",", "."))
        fee = round(pct * 10000)
        if fee <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid value. Enter a percentage, e.g.: 0.05 or 1")
        return
    await _apply_fee(fee, state, message)


@router.message(OpenPosFSM.enter_token_addr)
async def fsm_enter_token_addr(message: Message, state: FSMContext):
    addr = message.text.strip()
    if not (addr.startswith("0x") and len(addr) == 42):
        await message.answer("❌ Invalid address (0x + 40 chars). Try again:")
        return
    data = await state.get_data()
    quote_addr = data.get("pending_quote_addr", ZERO_ADDR)
    await _proceed_to_confirm(message, state, addr, quote_addr)


# ── Confirm & execute ─────────────────────────────────────────────────────────

async def _proceed_to_confirm(msg, state: FSMContext, token_addr: str, quote_addr: str):
    from web3 import Web3

    # Sort: currency0 < currency1 (Uniswap requirement)
    a = Web3.to_checksum_address(token_addr)
    b = Web3.to_checksum_address(quote_addr) if quote_addr else ZERO_ADDR
    currency0, currency1 = (a, b) if a.lower() < b.lower() else (b, a)

    data = await state.get_data()
    await state.update_data(currency0=currency0, currency1=currency1)

    levels = data["ladder_levels"]
    wallet = data["wallet_address"]
    chain = data["chain"]
    fee = data["fee"]

    text = (
        f"<b>Confirm opening positions</b>\n\n"
        f"Wallet: <code>{wallet[:8]}…{wallet[-4:]}</code>\n"
        f"Chain: {chain}  |  Fee: {fee/10000:.2f}%\n"
        f"Pair: <code>{currency0[:8]}…</code> / <code>{currency1[:8]}…</code>\n\n"
        f"Levels: <b>{len(levels)}</b>\n"
        + "\n".join(
            f"  #{i+1}: <b>${lvl['amount']:,.2f}</b>  "
            f"[{lvl['lower']:.4f} – {lvl['upper']:.4f}]"
            for i, lvl in enumerate(levels)
        )
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Open all", callback_data="opconfirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="opcancel"),
    ]])
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "opconfirm")
async def cb_confirm_open(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    required = ["ladder_levels", "wallet_address", "chain", "fee", "tick_spacing",
                "currency0", "currency1"]
    if not all(k in data for k in required):
        await call.answer("Data expired. Please build a new ladder.", show_alert=True)
        return

    await state.set_state(None)
    await call.answer()

    status_msg = await call.message.answer("🚀 Opening positions…")

    from web3 import Web3
    from modules.v4_executor import open_ladder_positions
    from modules.rpc_tracker import ERC20_ABI
    from config import RPC_URLS

    chain      = data["chain"]
    currency0  = data["currency0"]
    currency1  = data["currency1"]
    fee        = data["fee"]
    tick_sp    = data["tick_spacing"]
    wallet_addr = data["wallet_address"]
    levels     = data["ladder_levels"]

    try:
        account = get_account(call.from_user.id, wallet_addr)
    except Exception as e:
        await status_msg.edit_text(f"❌ Wallet error: {e}")
        return

    w3 = Web3(Web3.HTTPProvider(RPC_URLS[chain]))
    try:
        dec0 = _get_decimals(w3, currency0)
        dec1 = _get_decimals(w3, currency1)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to get token decimals: {e}")
        return

    log_lines: list[str] = []
    loop = asyncio.get_running_loop()

    def progress(msg_text: str):
        log_lines.append(msg_text)
        text = "\n".join(log_lines[-12:])

        async def _edit():
            try:
                await status_msg.edit_text(text, parse_mode="Markdown")
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(_edit(), loop)

    try:
        results = await asyncio.to_thread(
            open_ladder_positions,
            chain, account,
            currency0, currency1,
            fee, tick_sp,
            ZERO_ADDR,   # hooks = zero (no hooks)
            dec0, dec1,
            levels,
            0.05,
            progress,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Critical error: {e}")
        return

    ok   = sum(1 for r in results if r["error"] is None)
    fail = len(results) - ok
    summary = (
        f"<b>Done!</b>  ✅{ok} / ❌{fail}\n\n"
        + "\n".join(
            f"#{r['level']}: ✅ <code>{r['tx'][:12]}…</code>" if not r["error"]
            else f"#{r['level']}: ❌ {r['error']}"
            for r in results
        )
    )
    await call.message.answer(summary, parse_mode="HTML")


@router.callback_query(F.data == "opcancel")
async def cb_cancel_open(call: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await call.message.edit_text("Cancelled.")
    await call.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_addr(token: str, chain: str) -> str | None:
    """Return contract address for token (ticker or 0x address). None if unknown."""
    if token.startswith("0x") and len(token) == 42:
        return token
    return KNOWN_TOKENS.get(chain, {}).get(token.upper())


def _get_decimals(w3, addr: str) -> int:
    if addr.lower() == ZERO_ADDR:
        return 18
    from modules.rpc_tracker import ERC20_ABI
    t = w3.eth.contract(address=w3.to_checksum_address(addr), abi=ERC20_ABI)
    return t.functions.decimals().call()
