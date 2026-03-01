#!/usr/bin/env python3
"""
YouTube Telegram Bot - Video Downloader & Summarizer
Advanced Features: Search, Channel Info, Latest/Top Videos, Live Capture
Modular Architecture with Download Queue, Progress Tracking, and Rate Limiting
"""

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery, InlineQuery

from bot.config import config
from bot.handlers import commands, messages, callbacks, inline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# --- Authorization Middleware ---

async def authorize_user(event, handler):
    """Check if user is authorized to use the bot."""
    user_id = None
    
    if isinstance(event, Message):
        user_id = event.from_user.id
    elif isinstance(event, CallbackQuery):
        user_id = event.from_user.id
    elif isinstance(event, InlineQuery):
        user_id = event.from_user.id
    
    if user_id is None:
        return
    
    if user_id not in config.ALLOWED_USER_IDS:
        if isinstance(event, Message):
            await event.answer("⛔️ Sorry, you're not authorized to use this bot.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ Unauthorized", show_alert=True)
        elif isinstance(event, InlineQuery):
            await event.answer(results=[], switch_pm_text="Unauthorized. Click to join.", switch_pm_parameter="auth")
        return
    
    return await handler(event)


# --- Bot Setup ---

async def main():
    """Main entry point."""
    # Load and validate configuration
    config.load()
    config.validate()
    
    # Initialize bot
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Create dispatcher
    dp = Dispatcher()
    
    # Include routers
    dp.message.middleware(authorize_user)
    dp.callback_query.middleware(authorize_user)
    dp.inline_query.middleware(authorize_user)
    
    dp.include_router(commands.router)
    dp.include_router(messages.router)
    dp.include_router(callbacks.router)
    dp.include_router(inline.router)
    
    # Delete webhook and start polling
    await bot.delete_webhook(drop_pending_updates=True)
    
    logger.info("Bot starting...")
    logger.info(f"Allowed users: {config.ALLOWED_USER_IDS}")
    logger.info(f"Downloads directory: {config.DOWNLOADS_DIR}")
    
    # Start polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
