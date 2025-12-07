import asyncio
import logging
import sys
import os

# Add project root to python path
sys.path.append(os.getcwd())

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import settings
from core.session import async_engine
from bot.handlers import start, profile, browsing

async def main():
    # Initialize Logging
    logging.basicConfig(level=logging.INFO)

    # Initialize Bot and Dispatcher
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Register Routers (Handlers)
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(browsing.router)

    print("Bot started!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")

