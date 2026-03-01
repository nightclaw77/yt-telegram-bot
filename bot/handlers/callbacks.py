"""Callback query handlers for Telegram bot."""
import asyncio
import logging
from aiogram import Router, Bot
from aiogram.types import CallbackQuery, FSInputFile

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.services.downloader import DownloadManager
from bot.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
download_manager = DownloadManager.get_instance()
summarizer = SummarizerService()


@router.callback_query()
async def handle_callback(callback: CallbackQuery, bot: Bot):
    """Handle callback queries from inline buttons."""
    parts = callback.data.split("|")
    action, url = parts[0], parts[1]
    
    if action == "info":
        from bot.handlers.messages import show_video_options
        await show_video_options(callback.message, url, bot)
        await callback.answer()
        return

    if action == "dl_video":
        format_id = parts[2] if len(parts) > 2 else "best"
        await handle_video_download(callback, url, format_id, bot)
    elif action == "dl_audio":
        await handle_audio_download(callback, url, bot)
    elif action == "summary":
        await handle_summary(callback, url, bot)
    elif action == "capture":
        await handle_live_capture(callback, url, bot)

    await callback.answer()


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
