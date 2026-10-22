import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN
from handlers import start, ladder, tracker, wallets, open_positions, shared
from db import pool
from modules import wallet_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COMMANDS = [
    BotCommand(command="new_ladder", description="Build an LP range ladder"),
    BotCommand(command="wallets",    description="Manage wallets (add / remove)"),
    BotCommand(command="track",      description="Add a wallet to track positions"),
    BotCommand(command="report",     description="Full report on all LP positions"),
    BotCommand(command="strategies", description="List positions by wallet"),
    BotCommand(command="wallet",     description="Switch the active tracked wallet"),
    BotCommand(command="alerts",     description="Range enter / exit alerts"),
    BotCommand(command="link",       description="Connect the browser extension"),
    BotCommand(command="help",       description="Show help"),
]


async def main():
    wallet_manager.migrate_wallets()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(ladder.router)
    dp.include_router(open_positions.router)
    dp.include_router(wallets.router)
    dp.include_router(tracker.router)
    dp.include_router(shared.router)

    await bot.set_my_commands(COMMANDS)
    if pool.enabled():
        from api import server
        from modules import sync
        await pool.init()
        from db import migrate
        await migrate.import_users_json()
        await server.start()
        sync.notify = lambda uid, events: shared.deliver_alerts(bot, uid, events)
        asyncio.create_task(sync.run_forever())
        logging.info("Shared DB + API started")
    logging.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
