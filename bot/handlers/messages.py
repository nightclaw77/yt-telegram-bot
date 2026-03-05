"""Message handlers for Telegram bot."""
import asyncio
import logging
from pathlib import Path
from aiogram import Router, Bot, F
from aiogram.types import Message, FSInputFile

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.services.compressor import CompressionService
from bot.utils.rate_limiter import RateLimiter
from bot.utils.validators import validate_youtube_url, is_channel_url
from bot.utils.url_shortener import shorten_callback

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
compressor_service = CompressionService()
rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)


@router.message(F.video)
async def handle_uploaded_video(message: Message, bot: Bot):
    """Compress Telegram-uploaded video files."""
    await _compress_and_send(message, bot, file_id=message.video.file_id, name_hint=message.video.file_name)


@router.message(F.document)
async def handle_uploaded_video_document(message: Message, bot: Bot):
    """Compress uploaded documents if they are video files."""
    mime = (message.document.mime_type or "").lower()
    if not mime.startswith("video/"):
        return
    await _compress_and_send(message, bot, file_id=message.document.file_id, name_hint=message.document.file_name)


async def _compress_and_send(message: Message, bot: Bot, file_id: str, name_hint: str | None = None):
    """Download a Telegram video, compress it, and upload back."""
    if not await rate_limiter.check_limit(message.from_user.id):
        await message.answer("⏳ Too many requests. Please wait a moment.")
        return

    status = await message.answer("🗜 در حال دریافت و فشرده‌سازی ویدیو... لطفاً کمی صبر کن")

    original_path = None
    compressed_path = None
    try:
        ext = ".mp4"
        if name_hint and "." in name_hint:
            ext = Path(name_hint).suffix or ".mp4"

        original_path = config.DOWNLOADS_DIR / f"incoming_{message.from_user.id}_{message.message_id}{ext}"

        tg_file = await bot.get_file(file_id)
        await bot.download(tg_file, destination=original_path)

        compressed_path = await compressor_service.compress_video(original_path)
        if not compressed_path:
            await status.edit_text("❌ فشرده‌سازی انجام نشد. احتمالاً فایل پشتیبانی نمی‌شود.")
            return

        before_mb = original_path.stat().st_size / (1024 * 1024)
        after_mb = compressed_path.stat().st_size / (1024 * 1024)

        await status.edit_text(
            f"✅ فشرده‌سازی کامل شد\n"
            f"قبل: {before_mb:.1f} MB\n"
            f"بعد: {after_mb:.1f} MB"
        )

        await message.answer_video(
            FSInputFile(str(compressed_path)),
            caption=f"🗜 Compressed\n{before_mb:.1f}MB → {after_mb:.1f}MB"
        )

    except Exception as e:
        logger.exception("Telegram video compression failed")
        await status.edit_text(f"❌ خطا در فشرده‌سازی: {e}")
    finally:
        try:
            if original_path and original_path.exists():
                original_path.unlink(missing_ok=True)
            if compressed_path and compressed_path.exists():
                compressed_path.unlink(missing_ok=True)
        except Exception:
            pass


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
            keyboard.append([InlineKeyboardButton(text="🔴 Join/Capture Live", callback_data=shorten_callback("info", v['url']))])
        response += "\n"

    response += "🆕 <b>Latest 3 Uploads:</b>\n"
    for i, v in enumerate(latest, 1):
        response += f"{i}. {v['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🆕 Latest #{i}", callback_data=shorten_callback("info", v['url']))])
    
    response += "\n🔥 <b>Top 3 Most Popular:</b>\n"
    for i, v in enumerate(top, 1):
        response += f"{i}. {v['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🔥 Top #{i}", callback_data=shorten_callback("info", v['url']))])

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
            InlineKeyboardButton(text="🎬 Best", callback_data=shorten_callback("dl_video", url, "best", "0")),
            InlineKeyboardButton(text="🗜 Best (Compressed)", callback_data=shorten_callback("dl_video", url, "best", "1"))
        ],
        [
            InlineKeyboardButton(text="🎬 720p", callback_data=shorten_callback("dl_video", url, "720", "0")),
            InlineKeyboardButton(text="🗜 720p (Compressed)", callback_data=shorten_callback("dl_video", url, "720", "1"))
        ],
        [
            InlineKeyboardButton(text="🎬 480p", callback_data=shorten_callback("dl_video", url, "480", "0")),
            InlineKeyboardButton(text="🗜 480p (Compressed)", callback_data=shorten_callback("dl_video", url, "480", "1"))
        ],
        [
            InlineKeyboardButton(text="🎵 Audio", callback_data=shorten_callback("dl_audio", url, "best", "0")),
            InlineKeyboardButton(text="🗜 Audio (Compressed)", callback_data=shorten_callback("dl_audio", url, "best", "1"))
        ],
        [InlineKeyboardButton(text="📝 AI Summary", callback_data=shorten_callback("summary", url, "xl"))]
    ]
    
    if info.get("is_live"):
        keyboard.append([InlineKeyboardButton(text="⏺️ Capture Live Stream", callback_data=shorten_callback("capture", url))])

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
