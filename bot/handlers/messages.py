"""Message handlers for Telegram bot."""
import asyncio
import logging
from aiogram import Router, Bot, F
from aiogram.types import Message

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.utils.rate_limiter import RateLimiter
from bot.utils.validators import validate_youtube_url, is_channel_url

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)


@router.message(F.text)
async def handle_text_input(message: Message, bot: Bot):
    """Handle incoming text messages - process URLs."""
    text = message.text.strip()
    
    # Check if it's a URL
    if not (text.startswith("http://") or text.startswith("https://")):
        return  # Not a URL, ignore
    
    # Check rate limit
    if not await rate_limiter.check_limit(message.from_user.id):
        await message.answer("⏳ Too many requests. Please wait a moment.")
        return
    
    # Validate YouTube URL
    if not validate_youtube_url(text):
        await message.answer("❌ Invalid YouTube URL. Please send a valid link.")
        return
    
    # Check if it's a channel URL
    if is_channel_url(text):
        await handle_channel_url(message, text, bot)
    else:
        # Default to video info
        await show_video_options(message, text, bot)


async def handle_channel_url(message: Message, url: str, bot: Bot):
    """Handle YouTube channel URL."""
    msg = await message.answer("📡 Probing channel data...")
    
    # Fetching data in parallel
    tasks = [
        youtube_service.get_channel_videos(url, "latest", 3),
        youtube_service.get_channel_videos(url, "top", 3),
        youtube_service.get_channel_videos(url, "live", 1)
    ]
    latest, top, live = await asyncio.gather(*tasks)
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    response = "🏢 <b>Channel Overview</b>\n\n"
    keyboard = []
    
    if live:
        response += "🔴 <b>LIVE NOW:</b>\n"
        for v in live:
            response += f"• {v['title']}\n"
            keyboard.append([InlineKeyboardButton(text="🔴 Join/Capture Live", callback_data=f"info|{v['url']}")])
        response += "\n"

    response += "🆕 <b>Latest 3 Uploads:</b>\n"
    for i, v in enumerate(latest, 1):
        response += f"{i}. {v['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🆕 Latest #{i}", callback_data=f"info|{v['url']}")])
    
    response += "\n🔥 <b>Top 3 Most Popular:</b>\n"
    for i, v in enumerate(top, 1):
        response += f"{i}. {v['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🔥 Top #{i}", callback_data=f"info|{v['url']}")])

    await msg.delete()
    await message.answer(response, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), disable_web_page_preview=True)


async def show_video_options(message: Message, url: str, bot: Bot):
    """Show video download options."""
    msg = await message.answer("⏳ Fetching video info...")
    info = await youtube_service.get_video_info(url)
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    if not info:
        await msg.edit_text("❌ Could not fetch info. Make sure it's a valid video link.")
        return
    
    keyboard = [
        [
            InlineKeyboardButton(text="🎬 Video (Best)", callback_data=f"dl_video|{url}|best"),
            InlineKeyboardButton(text="🎬 720p", callback_data=f"dl_video|{url}|best[height<=720]")
        ],
        [InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data=f"dl_audio|{url}|best")],
        [InlineKeyboardButton(text="📝 AI Summary", callback_data=f"summary|{url}|xl")]
    ]
    
    if info.get("is_live"):
        keyboard.append([InlineKeyboardButton(text="⏺️ Capture Live Stream", callback_data=f"capture|{url}")])

    await msg.delete()
    
    duration = info.get("duration", 0)
    duration_str = f"{duration//60}:{duration%60:02d}" if duration else "N/A"
    
    await message.answer(
        f"🎬 <b>{info['title']}</b>\n"
        f"👤 {info['uploader']}\n"
        f"⏱️ {duration_str}\n\n"
        f"Choose an action:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
