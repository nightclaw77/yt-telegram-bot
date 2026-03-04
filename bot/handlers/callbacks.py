"""Callback query handlers for Telegram bot."""
import asyncio
import logging
from aiogram import Router, Bot
from aiogram.types import CallbackQuery, FSInputFile

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.services.downloader import DownloadManager
from bot.services.summarizer import SummarizerService
from bot.utils.url_shortener import reconstruct_url, shorten_callback

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
download_manager = DownloadManager.get_instance()
summarizer = SummarizerService()


async def show_quality_options(user_id: int, url: str, mode: str, bot: Bot):
    """Show quality selection options for download."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    if mode == "audio":
        # Audio options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 MP3 (128k)", callback_data=shorten_callback("dl_audio", url, "bestaudio")),
                InlineKeyboardButton(text="🎵 MP3 (320k)", callback_data=shorten_callback("dl_audio", url, "bestaudio+"))
            ],
            [
                InlineKeyboardButton(text="🔙 Back", callback_data=shorten_callback("select_format", url, "video"))
            ]
        ])
        await bot.send_message(
            chat_id=user_id,
            text="🎧 <b>Audio Quality</b>\n\nSelect audio quality:",
            reply_markup=keyboard
        )
    else:
        # Video quality options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🖥️ 4K (2160p)", callback_data=shorten_callback("dl_video", url, "2160")),
                InlineKeyboardButton(text="🖥️ 1080p", callback_data=shorten_callback("dl_video", url, "1080"))
            ],
            [
                InlineKeyboardButton(text="🖥️ 720p", callback_data=shorten_callback("dl_video", url, "720")),
                InlineKeyboardButton(text="🖥️ 480p", callback_data=shorten_callback("dl_video", url, "480"))
            ],
            [
                InlineKeyboardButton(text="⚡ Best Available", callback_data=shorten_callback("dl_video", url, "best")),
            ],
            [
                InlineKeyboardButton(text="🎵 Audio Only (MP3)", callback_data=shorten_callback("select_format", url, "audio"))
            ]
        ])
        
        # Get video info for title
        info = await youtube_service.get_video_info(url)
        title = info.get("title", "Video") if info else "Video"
        
        await bot.send_message(
            chat_id=user_id,
            text=f"🎬 <b>{title[:50]}...</b>\n\n"
                 f"📥 <b>Select Quality:</b>",
            reply_markup=keyboard
        )


@router.callback_query()
async def handle_callback(callback: CallbackQuery, bot: Bot):
    """Handle callback queries from inline buttons."""
    parts = callback.data.split("|")
    action = parts[0]
    video_id_or_url = parts[1] if len(parts) > 1 else ""
    
    # Reconstruct full URL if it's a short video ID (11 chars)
    if len(video_id_or_url) == 11 and video_id_or_url.replace("-", "").replace("_", "").isalnum():
        url = reconstruct_url(video_id_or_url)
    else:
        url = video_id_or_url
    
    # For inline mode callbacks, we need to answer with a message
    # instead of editing the original message (which might not exist in inline context)
    is_inline = callback.message is None or callback.inline_message_id is not None
    
    if action == "info":
        from bot.handlers.messages import show_video_options
        if is_inline:
            # For inline callbacks, we need to send a new message to the user
            await bot.send_message(
                chat_id=callback.from_user.id,
                text="⏳ Loading video options..."
            )
            # Actually get the info and show options
            info = await youtube_service.get_video_info(url)
            if info:
                from bot.handlers.messages import show_video_options
                await show_video_options_by_id(callback.from_user.id, url, bot)
        else:
            from bot.handlers.messages import show_video_options
            await show_video_options(callback.message, url, bot)
        await callback.answer()
        return

    if action == "select_format":
        # Show quality selection menu
        mode = parts[2] if len(parts) > 2 else "video"
        await show_quality_options(callback.from_user.id, url, mode, bot)
        await callback.answer()
        return

    if action == "dl_video":
        quality = parts[2] if len(parts) > 2 else "best"
        
        # Map shorthand quality to yt-dlp format
        format_map = {
            "2160": "bestvideo[height<=2160]",
            "1080": "bestvideo[height<=1080]+bestaudio/best",
            "720": "bestvideo[height<=720]+bestaudio/best",
            "480": "bestvideo[height<=480]+bestaudio/best",
            "best": "best"
        }
        format_id = format_map.get(quality, "best")
        
        # Send to user's DM instead of trying to reply to inline message
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⏳ Starting download..."
        )
        await handle_video_download_inline(callback, url, format_id, bot)
    elif action == "dl_audio":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⏳ Extracting audio..."
        )
        await handle_audio_download_inline(callback, url, bot)
    elif action == "summary":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⏳ Generating AI summary..."
        )
        await handle_summary_inline(callback, url, bot)
    elif action == "capture":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⏳ Starting live capture..."
        )
        await handle_live_capture_inline(callback, url, bot)

    await callback.answer()


async def show_video_options_by_id(user_id: int, url: str, bot: Bot):
    """Show video options by sending to user's DM."""
    info = await youtube_service.get_video_info(url)
    if not info:
        await bot.send_message(chat_id=user_id, text="❌ Could not fetch video info.")
        return
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = [
        [
            InlineKeyboardButton(text="🎬 Video (best)", callback_data=shorten_callback("dl_video", url, "best")),
            InlineKeyboardButton(text="🎬 Video (720p)", callback_data=shorten_callback("dl_video", url, "720"))
        ],
        [
            InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data=shorten_callback("dl_audio", url, "bestaudio")),
            InlineKeyboardButton(text="📝 AI Summary", callback_data=shorten_callback("summary", url, "xl"))
        ]
    ]
    
    if info.get("is_live"):
        keyboard.append([InlineKeyboardButton(text="⏺️ Capture Live Stream", callback_data=shorten_callback("capture", url))])

    duration = info.get("duration", 0)
    duration_str = f"{duration//60}:{duration%60:02d}" if duration else "N/A"
    
    await bot.send_message(
        chat_id=user_id,
        text=f"🎬 <b>{info['title']}</b>\n"
             f"👤 {info['uploader']}\n"
             f"⏱️ {duration_str}\n\n"
             f"Choose an action:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


async def handle_video_download(callback: CallbackQuery, url: str, format_id: str, bot: Bot):
    """Handle video download with progress tracking."""
    status_msg = await callback.message.answer("⏳ Downloading video...")
    
    # Create progress callback
    async def progress_callback(progress: dict):
        percent = progress.get("percent", 0)
        speed = progress.get("speed", "N/A")
        eta = progress.get("eta", "N/A")
        try:
            await bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=status_msg.message_id,
                text=f"📥 Downloading... {percent:.1f}%\nSpeed: {speed}\nETA: {eta}"
            )
        except Exception:
            pass
    
    # Start download
    task_id = download_manager.add_download(
        user_id=callback.from_user.id,
        url=url,
        format_id=format_id,
        mode="video",
        progress_callback=progress_callback
    )
    
    # Wait for download to complete
    path = await download_manager.wait_for_download(task_id)
    
    if path:
        # Upload with retry logic
        success = await upload_with_retry(bot, callback.message, "video", path)
        
        if success:
            # Save to history
            from bot.database.models import Database
            db = Database()
            await db.init()
            info = await youtube_service.get_video_info(url)
            await db.add_download(
                user_id=callback.from_user.id,
                url=url,
                title=info.get("title", "Unknown") if info else "Video",
                status="completed",
                file_path=path
            )
        else:
            await callback.message.answer("❌ Upload failed. File retained for retry.")
            return  # Don't delete file if upload failed
    else:
        await callback.message.answer("❌ Download failed.")
        
        # Save failed download to history
        from bot.database.models import Database
        db = Database()
        await db.init()
        info = await youtube_service.get_video_info(url)
        await db.add_download(
            user_id=callback.from_user.id,
            url=url,
            title=info.get("title", "Unknown") if info else "Video",
            status="failed",
            file_path=None
        )
    
    await status_msg.delete()


async def handle_audio_download(callback: CallbackQuery, url: str, bot: Bot):
    """Handle audio download."""
    status_msg = await callback.message.answer("⏳ Downloading audio...")
    
    # Create progress callback
    async def progress_callback(progress: dict):
        percent = progress.get("percent", 0)
        try:
            await bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=status_msg.message_id,
                text=f"🎵 Downloading audio... {percent:.1f}%"
            )
        except Exception:
            pass
    
    # Start download
    task_id = download_manager.add_download(
        user_id=callback.from_user.id,
        url=url,
        format_id="bestaudio",
        mode="audio",
        progress_callback=progress_callback
    )
    
    # Wait for download to complete
    path = await download_manager.wait_for_download(task_id)
    
    if path:
        success = await upload_with_retry(bot, callback.message, "audio", path)
        
        if success:
            from bot.database.models import Database
            db = Database()
            await db.init()
            info = await youtube_service.get_video_info(url)
            await db.add_download(
                user_id=callback.from_user.id,
                url=url,
                title=info.get("title", "Unknown") if info else "Audio",
                status="completed",
                file_path=path
            )
        else:
            await callback.message.answer("❌ Upload failed. File retained for retry.")
            return
    else:
        await callback.message.answer("❌ Audio extraction failed.")
    
    await status_msg.delete()


async def handle_summary(callback: CallbackQuery, url: str, bot: Bot):
    """Handle AI summary generation."""
    status_msg = await callback.message.answer("⏳ Generating AI summary (this takes time)...")
    
    try:
        summary = await summarizer.summarize(url)
        
        if summary:
            # Chunk sending for long summaries
            for i in range(0, len(summary), 4000):
                await callback.message.answer(f"📝 <b>Summary:</b>\n\n{summary[i:i+4000]}")
        else:
            await callback.message.answer("❌ Could not generate summary.")
    except Exception as e:
        await callback.message.answer(f"❌ Error: {e}")
    
    await status_msg.delete()


async def handle_live_capture(callback: CallbackQuery, url: str, bot: Bot):
    """Handle live stream capture."""
    status_msg = await callback.message.answer("⏳ Starting live stream capture...")
    
    task_id = download_manager.add_download(
        user_id=callback.from_user.id,
        url=url,
        format_id="best",
        mode="live",
        progress_callback=None
    )
    
    path = await download_manager.wait_for_download(task_id)
    
    if path:
        success = await upload_with_retry(bot, callback.message, "video", path)
        if success:
            await callback.message.answer("✅ Live stream captured and uploaded!")
        else:
            await callback.message.answer("❌ Upload failed. File retained.")
            return
    else:
        await callback.message.answer("❌ Live capture failed.")
    
    await status_msg.delete()


async def upload_with_retry(bot: Bot, message, media_type: str, path: str, max_retries: int = 3):
    """Upload file with retry logic."""
    import os
    
    for attempt in range(max_retries):
        try:
            if media_type == "video":
                await message.answer_video(FSInputFile(path))
            elif media_type == "audio":
                await message.answer_audio(FSInputFile(path))
            
            # Only remove file after successful upload
            if os.path.exists(path):
                os.remove(path)
            return True
            
        except Exception as e:
            logger.warning(f"Upload attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await message.answer(f"⚠️ Upload failed. Retrying... ({attempt + 2}/{max_retries})")
                await asyncio.sleep(2)
            else:
                logger.error(f"Upload failed after {max_retries} attempts: {e}")
                return False
    
    return False


# --- Inline mode handlers (send to user's DM) ---

async def handle_video_download_inline(callback: CallbackQuery, url: str, format_id: str, bot: Bot):
    """Handle video download for inline mode - sends to user's DM."""
    user_id = callback.from_user.id
    
    # Get video info for title
    info = await youtube_service.get_video_info(url)
    title = info.get("title", "Video")[:40] + "..." if info else "Video"
    
    # Send initial status message
    status_msg = await bot.send_message(
        user_id, 
        f"🎬 <b>{title}</b>\n\n"
        f"📥 Status: <b>Starting download...</b>\n"
        f"⏳ Please wait..."
    )
    
    # Create progress callback
    async def progress_callback(progress: dict):
        percent = progress.get("percent", 0)
        speed = progress.get("speed", "N/A")
        eta = progress.get("eta", "N/A")
        
        # Create progress bar
        bar_length = 15
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=status_msg.message_id,
                text=f"🎬 <b>{title}</b>\n\n"
                     f"📥 Status: <b>Downloading...</b>\n"
                     f" Progress: [{bar}] {percent:.0f}%\n"
                     f" Speed: {speed} | ETA: {eta}"
            )
        except Exception:
            pass
    
    # Send "processing" message
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=status_msg.message_id,
            text=f"🎬 <b>{title}</b>\n\n"
                 f"📥 Status: <b>Processing...</b>\n"
                 f"⏳ Connecting to YouTube..."
        )
    except:
        pass
    
    task_id = download_manager.add_download(
        user_id=user_id,
        url=url,
        format_id=format_id,
        mode="video",
        progress_callback=progress_callback
    )
    
    path = await download_manager.wait_for_download(task_id)
    chat_id = user_id
    
    if path:
        # Update to uploading status
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=status_msg.message_id,
                text=f"🎬 <b>{title}</b>\n\n"
                     f"📤 Status: <b>Uploading to Telegram...</b>\n"
                     f"⏳ Please wait, this may take time for large files..."
            )
        except:
            pass
        
        success = await upload_with_retry(bot, status_msg, "video", path)
        
        if success:
            from bot.database.models import Database
            db = Database()
            await db.init()
            await db.add_download(
                user_id=user_id,
                url=url,
                title=info.get("title", "Unknown") if info else "Video",
                status="completed",
                file_path=path
            )
        else:
            await bot.send_message(user_id, "❌ Upload failed. File retained for retry.")
            return
    else:
        await bot.send_message(
            user_id, 
            f"❌ <b>Download Failed!</b>\n\n"
            f"Video: {title}\n"
            f"⚠️ The video might be unavailable or age-restricted."
        )
        
        from bot.database.models import Database
        db = Database()
        await db.init()
        await db.add_download(
            user_id=user_id,
            url=url,
            title=info.get("title", "Unknown") if info else "Video",
            status="failed",
            file_path=None
        )
    
    try:
        await status_msg.delete()
    except:
        pass


async def handle_audio_download_inline(callback: CallbackQuery, url: str, bot: Bot):
    """Handle audio download for inline mode."""
    user_id = callback.from_user.id
    status_msg = await bot.send_message(user_id, "⏳ Downloading audio...")
    
    async def progress_callback(progress: dict):
        percent = progress.get("percent", 0)
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=status_msg.message_id,
                text=f"🎵 Downloading audio... {percent:.1f}%"
            )
        except Exception:
            pass
    
    task_id = download_manager.add_download(
        user_id=user_id,
        url=url,
        format_id="bestaudio",
        mode="audio",
        progress_callback=progress_callback
    )
    
    path = await download_manager.wait_for_download(task_id)
    
    if path:
        success = await upload_with_retry(bot, status_msg, "audio", path)
        
        if success:
            from bot.database.models import Database
            db = Database()
            await db.init()
            info = await youtube_service.get_video_info(url)
            await db.add_download(
                user_id=user_id,
                url=url,
                title=info.get("title", "Unknown") if info else "Audio",
                status="completed",
                file_path=path
            )
        else:
            await bot.send_message(user_id, "❌ Upload failed. File retained.")
            return
    else:
        await bot.send_message(user_id, "❌ Audio extraction failed.")
    
    try:
        await status_msg.delete()
    except:
        pass


async def handle_summary_inline(callback: CallbackQuery, url: str, bot: Bot):
    """Handle AI summary for inline mode."""
    user_id = callback.from_user.id
    status_msg = await bot.send_message(user_id, "⏳ Generating AI summary (this may take a while)...")
    
    try:
        summary = await summarizer.summarize(url)
        
        if summary:
            # Delete status message first
            try:
                await status_msg.delete()
            except:
                pass
            
            # Send summary in chunks
            for i in range(0, len(summary), 4000):
                await bot.send_message(
                    chat_id=user_id,
                    text=f"📝 <b>Summary:</b>\n\n{summary[i:i+4000]}",
                    parse_mode="HTML"
                )
        else:
            await bot.send_message(user_id, "❌ Could not generate summary.")
    except Exception as e:
        await bot.send_message(user_id, f"❌ Error: {e}")
    
    try:
        await status_msg.delete()
    except:
        pass


async def handle_live_capture_inline(callback: CallbackQuery, url: str, bot: Bot):
    """Handle live stream capture for inline mode."""
    user_id = callback.from_user.id
    status_msg = await bot.send_message(user_id, "⏳ Starting live stream capture...")
    
    task_id = download_manager.add_download(
        user_id=user_id,
        url=url,
        format_id="best",
        mode="live",
        progress_callback=None
    )
    
    path = await download_manager.wait_for_download(task_id)
    
    if path:
        success = await upload_with_retry(bot, status_msg, "video", path)
        if success:
            await bot.send_message(user_id, "✅ Live stream captured and uploaded!")
        else:
            await bot.send_message(user_id, "❌ Upload failed. File retained.")
            return
    else:
        await bot.send_message(user_id, "❌ Live capture failed.")
    
    try:
        await status_msg.delete()
    except:
        pass
