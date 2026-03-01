#!/usr/bin/env python3
"""
YouTube Telegram Bot - Video Downloader & Summarizer
Advanced Features: Search, Channel Info, Latest/Top Videos, Live Capture
"""

import os
import asyncio
import logging
import subprocess
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv("/root/.openclaw/workspace/projects/yt-telegram-bot/.env", override=True)

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = "8641216077:AAFVNANR7WWKdPpHHbntqZDgyzBycowmPXA"
ALLOWED_USER_IDS = [971043547]

# Paths
DOWNLOADS_DIR = Path("/root/.openclaw/workspace/projects/yt-telegram-bot/downloads")
YTSUMMARIZE_CLI = "/usr/local/bin/ytsummarize"

# Initialize bot
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
router = Router()

# --- UTILS ---

def check_user(func):
    """Decorator to check if user is allowed and cleanup kwargs"""
    async def wrapper(event, *args, **kwargs):
        user_id = event.from_user.id
        if user_id not in ALLOWED_USER_IDS:
            if isinstance(event, Message):
                await event.answer("⛔️ Sorry, you're not authorized to use this bot.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔️ Unauthorized", show_alert=True)
            return
        
        import inspect
        sig = inspect.signature(func)
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return await func(event, *args, **filtered_kwargs)
    return wrapper

async def get_video_info(url: str) -> dict:
    cmd = ["yt-dlp", "--dump-json", "--no-playlist", url]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            data = json.loads(stdout.decode())
            return {
                "id": data.get("id"),
                "title": data.get("title", "Unknown"),
                "duration": data.get("duration", 0),
                "uploader": data.get("uploader", "Unknown"),
                "is_live": data.get("is_live", False)
            }
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
    return None

async def search_youtube(query: str, limit: int = 5) -> list:
    cmd = ["yt-dlp", f"ytsearch{limit}:{query}", "--dump-json", "--flat-playlist"]
    results = []
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        for line in stdout.decode().splitlines():
            data = json.loads(line)
            results.append({
                "id": data.get("id"),
                "title": data.get("title"),
                "url": f"https://www.youtube.com/watch?v={data.get('id')}"
            })
    except Exception as e:
        logger.error(f"Search error: {e}")
    return results

async def get_channel_videos(channel_url: str, mode: str = "latest", limit: int = 3) -> list:
    # mode: latest, top, live
    cmd = ["yt-dlp", "--dump-json", "--flat-playlist", f"--playlist-end", str(limit)]
    
    if mode == "top":
        cmd.extend(["--order", "relevance"]) # relevance usually maps to top for channel lists
    
    if mode == "live":
        # Specific live stream probe
        target = channel_url.rstrip("/") + "/live"
        cmd = ["yt-dlp", "--dump-json", "--no-playlist", target]
    else:
        cmd.append(channel_url)
        
    videos = []
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        for line in stdout.decode().splitlines():
            if not line.strip(): continue
            data = json.loads(line)
            videos.append({
                "id": data.get("id"),
                "title": data.get("title"),
                "url": f"https://www.youtube.com/watch?v={data.get('id')}" if "watch" not in data.get("url", "") else data.get("url")
            })
    except Exception as e:
        logger.error(f"Channel fetch error ({mode}): {e}")
    return videos

async def download_task(url: str, format_id: str, mode: str):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M%S")
    output_template = str(DOWNLOADS_DIR / f"dl_{timestamp}_%(title)s.%(ext)s")
    
    cmd = ["yt-dlp", "-f", format_id, "--output", output_template, "--no-playlist", url]
    if mode == "audio":
        cmd.extend(["-x", "--audio-format", "mp3"])
    
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    
    if process.returncode == 0:
        # Get the actual file path (it might have changed extension)
        files = list(DOWNLOADS_DIR.glob(f"dl_{timestamp}_*"))
        if files:
            return str(files[0])
    return None

# --- HANDLERS ---

@router.message(Command("start"))
@check_user
async def cmd_start(message: Message):
    await message.answer(
        "🎬 <b>Night YouTube Bot</b>\n\n"
        "• Send a <b>Video URL</b> to download or summarize.\n"
        "• Send a <b>Channel URL</b> to see latest/top/live content.\n"
        "• Use <code>/search query</code> to find videos.\n"
        "• Use <code>/live url</code> to capture a live stream."
    )

@router.message(Command("search"))
@check_user
async def cmd_search(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("🔍 Usage: <code>/search keywords</code>")
        return
    
    msg = await message.answer(f"🔍 Searching for <i>'{command.args}'</i>...")
    results = await search_youtube(command.args)
    
    if not results:
        await msg.edit_text("❌ No results found.")
        return
    
    text = f"🔍 <b>Search results for:</b> {command.args}\n\n"
    keyboard = []
    for i, res in enumerate(results, 1):
        text += f"{i}. {res['title']}\n"
        keyboard.append([InlineKeyboardButton(text=f"🎥 Select #{i}", callback_data=f"info|{res['url']}")])
    
    await msg.delete()
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), disable_web_page_preview=True)

@router.message(F.text)
@check_user
async def handle_text_input(message: Message):
    text = message.text.strip()
    
    # Check if it's a URL
    if not (text.startswith("http://") or text.startswith("https://")):
        await message.answer("❌ Please send a valid link or use /search")
        return

    # Channel Check
    if "/channel/" in text or "/c/" in text or "/@" in text:
        msg = await message.answer("📡 Probing channel data...")
        
        # Fetching data in parallel
        tasks = [
            get_channel_videos(text, "latest", 3),
            get_channel_videos(text, "top", 3),
            get_channel_videos(text, "live", 1)
        ]
        latest, top, live = await asyncio.gather(*tasks)
        
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
        return

    # Default to Video Info
    await show_video_options(message, text)

async def show_video_options(message: Message, url: str):
    msg = await message.answer("⏳ Fetching video info...")
    info = await get_video_info(url)
    
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
    await message.answer(
        f"🎬 <b>{info['title']}</b>\n"
        f"👤 {info['uploader']}\n"
        f"⏱️ {info['duration']//60}:{info['duration']%60:02d}\n\n"
        f"Choose an action:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@router.callback_query()
@check_user
async def handle_callback(callback: CallbackQuery):
    parts = callback.data.split("|")
    action, url = parts[0], parts[1]
    
    if action == "info":
        await show_video_options(callback.message, url)
        await callback.answer()
        return

    if action == "dl_video":
        format_id = parts[2] if len(parts) > 2 else "best"
        status = await callback.message.answer("⏳ Downloading video...")
        path = await download_task(url, format_id, "video")
        if path:
            await callback.message.answer_video(FSInputFile(path))
            os.remove(path)
        else:
            await callback.message.answer("❌ Download failed.")
        await status.delete()

    elif action == "dl_audio":
        status = await callback.message.answer("⏳ Downloading audio...")
        path = await download_task(url, "bestaudio", "audio")
        if path:
            await callback.message.answer_audio(FSInputFile(path))
            os.remove(path)
        else:
            await callback.message.answer("❌ Audio extraction failed.")
        await status.delete()

    elif action == "summary":
        status = await callback.message.answer("⏳ Generating AI summary (this takes time)...")
        try:
            result = await asyncio.create_subprocess_exec(YTSUMMARIZE_CLI, url, "xl", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await result.communicate()
            summary = stdout.decode().strip()
            if summary:
                # Chunk sending
                for i in range(0, len(summary), 4000):
                    await callback.message.answer(f"📝 <b>Summary:</b>\n\n{summary[i:i+4000]}")
            else:
                await callback.message.answer("❌ Could not generate summary.")
        except Exception as e:
            await callback.message.answer(f"❌ Error: {e}")
        await status.delete()

    await callback.answer()

async def main():
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
