"""Command handlers for Telegram bot."""
import asyncio
import logging
from aiogram import Router, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command."""
    await message.answer(
        "🎬 <b>Night YouTube Bot</b>\n\n"
        "• Send a <b>Video URL</b> to download or summarize.\n"
        "• Send a <b>Channel URL</b> to see latest/top/live content.\n"
        "• Use <code>/search query</code> to find videos.\n"
        "• Use <code>/live url</code> to capture a live stream.\n"
        "• Use <code>/cancel</code> to cancel ongoing downloads.\n"
        "• Use <code>/history</code> to see download history."
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    await message.answer(
        "📖 <b>Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start - Start the bot\n"
        "/search <query> - Search for videos\n"
        "/live <url> - Capture a live stream\n"
        "/cancel - Cancel ongoing download\n"
        "/history - View download history\n"
        "/help - Show this help message\n\n"
        "<b>Supported URLs:</b>\n"
        "• YouTube video links\n"
        "• YouTube channel links\n"
        "• YouTube playlist links"
    )


@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject, bot: Bot):
    """Handle /search command."""
    # Check rate limit
    if not await rate_limiter.check_limit(message.from_user.id):
        await message.answer("⏳ Too many requests. Please wait a moment.")
        return
    
    if not command.args:
        await message.answer("🔍 Usage: <code>/search keywords</code>")
        return
    
    msg = await message.answer(f"🔍 Searching for <i>'{command.args}'</i>...")
    results = await youtube_service.search(command.args)
    
    if not results:
        await msg.edit_text("❌ No results found.")
        return
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    text = f"🔍 <b>Search results for:</b> {command.args}\n\n"
    keyboard = []
    for i, res in enumerate(results, 1):
        text += f"{i}. {res['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🎥 Select #{i}", callback_data=f"info|{res['url']}")])
    
    await msg.delete()
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), disable_web_page_preview=True)


@router.message(Command("history"))
async def cmd_history(message: Message):
    """Handle /history command to show download history."""
    from bot.database.models import Database
    
    db = Database()
    await db.init()
    history = await db.get_user_history(message.from_user.id, limit=10)
    
    if not history:
        await message.answer("📭 No download history yet.")
        return
    
    text = "📜 <b>Download History:</b>\n\n"
    for item in history:
        status_emoji = "✅" if item['status'] == 'completed' else "❌"
        text += f"{status_emoji} {item['title']}\n"
        text += f"   📅 {item['created_at']}\n\n"
    
    await message.answer(text)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, bot: Bot):
    """Handle /cancel command to cancel ongoing downloads."""
    from bot.services.downloader import DownloadManager
    
    download_manager = DownloadManager.get_instance()
    cancelled = download_manager.cancel_user_downloads(message.from_user.id)
    
    if cancelled:
        await message.answer("✅ Your download has been cancelled.")
    else:
        await message.answer("ℹ️ No active downloads to cancel.")
