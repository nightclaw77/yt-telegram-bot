"""Message handlers for Telegram bot."""
import asyncio
import logging
from pathlib import Path
from aiogram import Router, Bot, F
from aiogram.types import Message, FSInputFile

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.services.compressor import CompressionService
from bot.services.bale_bridge import bale_bridge_service
from bot.services.file_cache import file_cache_service
from bot.services.secure_package import create_secure_zip_many, DEFAULT_PASSWORD
from bot.utils.rate_limiter import RateLimiter
from bot.utils.validators import validate_youtube_url, is_channel_url
from bot.utils.url_shortener import shorten_callback

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
compressor_service = CompressionService()
rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)

# in-memory batch state per user
BATCH_STATE: dict[int, dict] = {}


def _is_batch_on(user_id: int) -> bool:
    return bool(BATCH_STATE.get(user_id, {}).get("active"))


async def batch_start(user_id: int):
    BATCH_STATE[user_id] = {"active": True, "items": []}


async def batch_clear(user_id: int):
    BATCH_STATE[user_id] = {"active": False, "items": []}


async def batch_add(user_id: int, file_id: str, name: str, size: int | None = None):
    state = BATCH_STATE.setdefault(user_id, {"active": True, "items": []})
    state["items"].append({"file_id": file_id, "name": name, "size": size or 0})


@router.message(F.video)
async def handle_uploaded_video(message: Message, bot: Bot):
    """Compress Telegram-uploaded video files or collect in batch mode."""
    user_id = message.from_user.id
    if _is_batch_on(user_id):
        await batch_add(user_id, message.video.file_id, message.video.file_name or f"video_{message.message_id}.mp4", message.video.file_size)
        count = len(BATCH_STATE[user_id]["items"])
        await message.answer(f"📦 به batch اضافه شد ({count} فایل).")
        return

    await _compress_and_send(message, bot, file_id=message.video.file_id, name_hint=message.video.file_name)


@router.message(F.document)
async def handle_uploaded_video_document(message: Message, bot: Bot):
    """Handle uploaded documents: batch collect or video-compress path."""
    user_id = message.from_user.id
    mime = (message.document.mime_type or "").lower()

    if _is_batch_on(user_id):
        await batch_add(user_id, message.document.file_id, message.document.file_name or f"doc_{message.message_id}.bin", message.document.file_size)
        count = len(BATCH_STATE[user_id]["items"])
        await message.answer(f"📦 به batch اضافه شد ({count} فایل).")
        return

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

        cache_key = file_cache_service.make_key("tg-upload", file_id, "compressed")
        cached_path = file_cache_service.get(cache_key)
        if cached_path:
            compressed_path = Path(cached_path)
            await status.edit_text("♻️ نسخه کش‌شده پیدا شد، در حال ارسال...")
        else:
            original_path = config.DOWNLOADS_DIR / f"incoming_{message.from_user.id}_{message.message_id}{ext}"

            tg_file = await bot.get_file(file_id)
            await bot.download(tg_file, destination=original_path)

            compressed_path = await compressor_service.compress_video(original_path)
        if not compressed_path:
            await status.edit_text("❌ فشرده‌سازی انجام نشد. احتمالاً فایل پشتیبانی نمی‌شود.")
            return

        if original_path and original_path.exists():
            before_mb = original_path.stat().st_size / (1024 * 1024)
        else:
            before_mb = compressed_path.stat().st_size / (1024 * 1024)

        after_mb = compressed_path.stat().st_size / (1024 * 1024)

        # Save compressed output in TTL cache
        cached_saved = file_cache_service.put(cache_key, compressed_path)
        if cached_saved:
            compressed_path = Path(cached_saved)

        await status.edit_text(
            f"✅ فشرده‌سازی کامل شد\n"
            f"قبل: {before_mb:.1f} MB\n"
            f"بعد: {after_mb:.1f} MB"
        )

        await message.answer_video(
            FSInputFile(str(compressed_path)),
            caption=f"🗜 Compressed\n{before_mb:.1f}MB → {after_mb:.1f}MB"
        )

        if bale_bridge_service.enabled:
            bale_ok = await bale_bridge_service.forward_file(
                compressed_path,
                "video",
                caption=f"Compressed mirror {before_mb:.1f}MB -> {after_mb:.1f}MB"
            )
            await message.answer("✅ نسخه بله هم ارسال شد." if bale_ok else "⚠️ ارسال نسخه بله ناموفق بود.")

    except Exception as e:
        logger.exception("Telegram video compression failed")
        await status.edit_text(f"❌ خطا در فشرده‌سازی: {e}")
    finally:
        try:
            if original_path and original_path.exists():
                original_path.unlink(missing_ok=True)
            if compressed_path and compressed_path.exists() and not file_cache_service.is_cache_file(compressed_path):
                compressed_path.unlink(missing_ok=True)
        except Exception:
            pass


@router.message(F.audio)
async def handle_uploaded_audio(message: Message, bot: Bot):
    """Collect uploaded audio in batch mode."""
    user_id = message.from_user.id
    if _is_batch_on(user_id):
        await batch_add(user_id, message.audio.file_id, message.audio.file_name or f"audio_{message.message_id}.mp3", message.audio.file_size)
        count = len(BATCH_STATE[user_id]["items"])
        await message.answer(f"📦 به batch اضافه شد ({count} فایل).")


async def send_batch_to_bale(message: Message, bot: Bot):
    """Create encrypted zip from collected files and send to Bale."""
    from bot.database.models import Database

    user_id = message.from_user.id
    state = BATCH_STATE.get(user_id, {"active": False, "items": []})
    items = state.get("items", [])
    if not items:
        await message.answer("📭 batch خالی است.")
        return

    status = await message.answer(f"📦 در حال آماده‌سازی batch ({len(items)} فایل)...")
    tmp_paths: list[Path] = []
    skipped: list[str] = []
    try:
        max_downloadable = 19 * 1024 * 1024  # guard for Telegram getFile practical limit
        for idx, item in enumerate(items, start=1):
            declared_size = int(item.get("size") or 0)
            safe_name = (item.get("name") or f"file_{idx}").replace("/", "_")

            if declared_size and declared_size > max_downloadable:
                skipped.append(f"{safe_name} ({declared_size / (1024*1024):.1f}MB)")
                continue

            try:
                tg_file = await bot.get_file(item["file_id"])
                local = config.DOWNLOADS_DIR / f"batch_{user_id}_{message.message_id}_{idx}_{safe_name}"
                await bot.download(tg_file, destination=local)
                tmp_paths.append(local)
            except Exception as e:
                skipped.append(f"{safe_name} ({e})")
                continue

        if not tmp_paths:
            msg = "❌ هیچ فایلی برای بسته‌بندی قابل دریافت نبود."
            if skipped:
                msg += "\nSkipped:\n- " + "\n- ".join(skipped[:5])
            await status.edit_text(msg)
            return

        db = Database(); await db.init()
        settings = await db.get_user_settings(user_id)
        pwd = settings.get("bale_password") or DEFAULT_PASSWORD
        comp = settings.get("compression_level", "medium")

        zip_path = await create_secure_zip_many(tmp_paths, comp, pwd)
        if not zip_path:
            await status.edit_text("❌ ساخت ZIP ناموفق بود.")
            return

        bale_ok = await bale_bridge_service.forward_file(zip_path, "document", caption=f"Batch package ({len(tmp_paths)} files)")
        result_msg = "✅ batch به بله ارسال شد." if bale_ok else "⚠️ ارسال batch به بله ناموفق بود."
        if skipped:
            result_msg += "\n\nSkipped:\n- " + "\n- ".join(skipped[:5])
        await status.edit_text(result_msg)
    except Exception as e:
        logger.exception("Batch send failed")
        await status.edit_text(f"❌ خطا در batch: {e}")
    finally:
        for p in tmp_paths:
            if p.exists():
                p.unlink(missing_ok=True)
        await batch_clear(user_id)


@router.message(F.text)
async def handle_text_input(message: Message, bot: Bot):
    """Handle incoming text messages - process URLs."""
    text = message.text.strip()

    if text == "📦 Batch ON":
        await batch_start(message.from_user.id)
        await message.answer("📦 Batch mode فعال شد. فایل‌ها را بفرست، بعد روی «📤 Batch Send» بزن.")
        return
    if text == "🧹 Batch Clear":
        await batch_clear(message.from_user.id)
        await message.answer("🧹 Batch پاک شد و غیرفعال شد.")
        return
    if text == "📤 Batch Send":
        await send_batch_to_bale(message, bot)
        return

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
    upload_date = info.get("upload_date", "")
    date_str = f" • {upload_date}" if upload_date else ""
    
    await message.answer(
        f"🎬 <b>{info['title']}</b>\n"
        f"👤 {info['uploader']}{date_str}\n"
        f"⏱️ {duration_str}\n\n"
        f"Choose an action:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
