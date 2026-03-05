#!/usr/bin/env python3
"""
YouTube Telegram Bot - Video Downloader & Summarizer
Advanced Features: Search, Channel Info, Latest/Top Videos, Live Capture
Modular Architecture with Download Queue, Progress Tracking, and Rate Limiting
"""

import asyncio
import logging
import os
import fcntl
import atexit
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Message, CallbackQuery, InlineQuery

from bot.config import config
from bot.handlers import commands, messages, callbacks, inline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

LOCK_FD = None


def acquire_single_instance_lock():
    """Prevent multiple bot instances from running concurrently."""
    global LOCK_FD
    lock_path = "/tmp/yt-bale-bot.instance.lock"
    LOCK_FD = open(lock_path, "w")
    try:
        fcntl.flock(LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOCK_FD.write(str(os.getpid()))
        LOCK_FD.flush()
    except OSError:
        raise RuntimeError("Another instance of yt-telegram-bot is already running")


def release_single_instance_lock():
    global LOCK_FD
    try:
        if LOCK_FD:
            fcntl.flock(LOCK_FD, fcntl.LOCK_UN)
            LOCK_FD.close()
            LOCK_FD = None
    except Exception:
        pass


# --- Authorization Middleware ---

async def authorize_user(handler, event, data):
    """
    Check if user is authorized to use the bot.
    aiogram 3.x middleware signature: (handler, event, data)
    """
    user_id = None
    
    if isinstance(event, Message):
        user_id = event.from_user.id
    elif isinstance(event, CallbackQuery):
        user_id = event.from_user.id
    elif isinstance(event, InlineQuery):
        user_id = event.from_user.id
    
    if user_id is None:
        return await handler(event, data)
    
    chat_id = None
    if isinstance(event, Message):
        chat_id = event.chat.id
    elif isinstance(event, CallbackQuery) and event.message:
        chat_id = event.message.chat.id

    allowed_user = user_id in config.ALLOWED_USER_IDS
    allowed_chat = chat_id in config.ALLOWED_CHAT_IDS if chat_id is not None else False

    if not (allowed_user or allowed_chat):
        if isinstance(event, Message):
            await event.answer("⛔️ Sorry, you're not authorized to use this bot.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ Unauthorized", show_alert=True)
        elif isinstance(event, InlineQuery):
            await event.answer(results=[], switch_pm_text="Unauthorized. Click to join.", switch_pm_parameter="auth")
        return
    
    return await handler(event, data)


# --- Bot Setup ---

async def main():
    """Main entry point."""
    acquire_single_instance_lock()
    atexit.register(release_single_instance_lock)

    # Load and validate configuration
    config.load()
    config.validate()
    
    # Cleanup old downloads on startup
    import os
    from pathlib import Path
    from datetime import datetime, timedelta
    
    try:
        downloads_dir = config.DOWNLOADS_DIR
        if downloads_dir.exists():
            cutoff = datetime.now() - timedelta(hours=24)
            deleted_count = 0
            for f in downloads_dir.iterdir():
                if f.is_file():
                    stat = f.stat()
                    modified = datetime.fromtimestamp(stat.st_mtime)
                    if modified < cutoff:
                        try:
                            f.unlink()
                            deleted_count += 1
                        except:
                            pass
            if deleted_count > 0:
                print(f"🗑️ Cleaned up {deleted_count} old download files on startup")
    except Exception as e:
        print(f"Startup cleanup warning: {e}")
    
    # Initialize bot (supports local telegram-bot-api server)
    session = None
    if config.TELEGRAM_API_BASE:
        api = TelegramAPIServer.from_base(config.TELEGRAM_API_BASE, is_local=True)
        session = AiohttpSession(api=api)

    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Create dispatcher
    dp = Dispatcher()
    
    # Register middleware
    dp.message.outer_middleware(authorize_user)
    dp.callback_query.outer_middleware(authorize_user)
    dp.inline_query.outer_middleware(authorize_user)
    
    # Include routers
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
