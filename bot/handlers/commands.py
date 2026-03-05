"""Command handlers for Telegram bot."""
import asyncio
import logging
from aiogram import Router, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.utils.rate_limiter import RateLimiter
from bot.utils.url_shortener import shorten_callback

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    """Handle /start command and deep links."""
    # Check for deep links (start parameters)
    if command.args:
        payload = command.args
        if payload.startswith("dl_"):
            video_id = payload[3:]
            url = f"https://www.youtube.com/watch?v={video_id}"
            from bot.handlers.messages import show_video_options
            await show_video_options(message, url)
            return
        elif payload.startswith("sum_"):
            video_id = payload[4:]
            url = f"https://www.youtube.com/watch?v={video_id}"
            from bot.handlers.callbacks import handle_summary_request
            # We simulate a callback for the summary
            await message.answer(f"⏳ Processing AI summary for video ID: {video_id}")
            # Instead of callback, we call the service directly if possible or show options
            from bot.handlers.messages import show_video_options
            await show_video_options(message, url)
            return

    await message.answer(
        "🎬 <b>Night YouTube Bot</b>\n\n"
        "• Send a <b>YouTube Video URL</b> to download or summarize.\n"
        "• Send a <b>Channel URL</b> to see latest/top/live content.\n"
        "• Send any <b>Telegram video file</b> (or video document) to compress it.\n"
        "• Use <code>/search query</code> to find videos.\n"
        "• Use <code>/live url</code> to capture a live stream.\n"
        "• Use <code>/cancel</code> to cancel ongoing downloads.\n"
        "• Use <code>/history</code> to see download history.\n"
        "• Use <code>/settings</code> for Bale mode/encryption/compression.\n\n"
        "✨ <b>Inline Mode:</b> Type <code>@Night77_tube_bot query</code> in any chat to search!"
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
        "<b>Supported input:</b>\n"
        "• YouTube video links\n"
        "• YouTube channel links\n"
        "• YouTube playlist links\n"
        "• Telegram uploaded video files (compression)"
    )


def _settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    bale_mode = settings.get("bale_mode", "auto")
    bale_encrypt = bool(settings.get("bale_encrypt", 1))
    comp = settings.get("compression_level", "medium")

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"Bale: {'🟢 Auto' if bale_mode=='auto' else '🟡 Manual'}", callback_data="settings_toggle_mode"),
            InlineKeyboardButton(text=f"Encrypt: {'🔐 ON' if bale_encrypt else '🔓 OFF'}", callback_data="settings_toggle_encrypt"),
        ],
        [
            InlineKeyboardButton(text=f"Compression: {comp}", callback_data="settings_cycle_compression"),
        ],
    ])


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    from bot.database.models import Database
    db = Database()
    await db.init()
    settings = await db.get_user_settings(message.from_user.id)
    await message.answer(
        "⚙️ <b>Settings</b>\n\n"
        "• Bale mode: Auto = send immediately | Manual = show button\n"
        "• Encrypt: password-protected ZIP for Bale-only transfer\n"
        "• Compression profile affects compressed-audio defaults",
        reply_markup=_settings_keyboard(settings)
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
        upload_date = res.get('upload_date', '')
        date_str = f" • {upload_date}" if upload_date else ""
        text += f"{i}. {res['title']}{date_str}\n"
        keyboard.append([InlineKeyboardButton(text=f"🎥 Select #{i}", callback_data=shorten_callback("info", res['url']))])
    
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


@router.message(Command("downloads"))
async def cmd_downloads(message: Message, bot: Bot):
    """Handle /downloads command to show list of downloaded files."""
    import os
    from pathlib import Path
    from datetime import datetime
    
    downloads_dir = config.DOWNLOADS_DIR
    
    if not downloads_dir.exists():
        await message.answer("📂 No downloads folder found.")
        return
    
    # Get all files
    files = []
    for f in downloads_dir.iterdir():
        if f.is_file():
            stat = f.stat()
            size_mb = stat.st_size / (1024 * 1024)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            files.append({
                "name": f.name,
                "size": f"{size_mb:.1f} MB",
                "date": modified,
                "path": str(f)
            })
    
    if not files:
        await message.answer("📂 No downloaded files found.")
        return
    
    # Sort by date (newest first)
    files.sort(key=lambda x: x["date"], reverse=True)
    
    # Show files with options to delete
    text = f"📂 <b>Downloaded Files</b> ({len(files)} files)\n\n"
    
    for i, f in enumerate(files[:10], 1):
        text += f"{i}. <code>{f['name'][:40]}...</code>\n"
        text += f"   📏 {f['size']} | 📅 {f['date']}\n\n"
    
    # Add total size
    total_size = sum(f["size_mb"] for f in [{"size_mb": float(f["size"].replace(" MB", ""))} for f in files])
    text += f"💾 <b>Total: {total_size:.1f} MB</b>\n\n"
    text += "Use /cleanup to delete all files."
    
    await message.answer(text)


@router.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Handle /cleanup command to delete old downloaded files."""
    import os
    from pathlib import Path
    from datetime import datetime, timedelta
    
    downloads_dir = config.DOWNLOADS_DIR
    
    if not downloads_dir.exists():
        await message.answer("📂 No downloads folder found.")
        return
    
    # Delete files older than 24 hours
    cutoff = datetime.now() - timedelta(hours=24)
    deleted_count = 0
    deleted_size = 0
    
    for f in downloads_dir.iterdir():
        if f.is_file():
            stat = f.stat()
            modified = datetime.fromtimestamp(stat.st_mtime)
            
            if modified < cutoff:
                size_mb = stat.st_size / (1024 * 1024)
                try:
                    f.unlink()
                    deleted_count += 1
                    deleted_size += size_mb
                except Exception as e:
                    logger.warning(f"Could not delete {f.name}: {e}")
    
    if deleted_count > 0:
        await message.answer(
            f"✅ <b>Cleanup Complete!</b>\n\n"
            f"🗑️ Deleted: {deleted_count} files\n"
            f"💾 Freed: {deleted_size:.1f} MB\n\n"
            f"Files older than 24 hours were removed."
        )
    else:
        await message.answer("✅ No old files to clean up.")
