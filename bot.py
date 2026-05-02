import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN
from handlers import start, ladder, tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COMMANDS = [
    BotCommand(command="new_ladder", description="Построить лестницу LP диапазонов"),
    BotCommand(command="track",      description="Добавить кошелёк для трекинга позиций"),
    BotCommand(command="report",     description="PnL отчёт по всем LP позициям"),
    BotCommand(command="strategies", description="Список позиций по кошельку"),
    BotCommand(command="help",       description="Справка по командам"),
]


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(ladder.router)
    dp.include_router(tracker.router)

    await bot.set_my_commands(COMMANDS)
    logging.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
