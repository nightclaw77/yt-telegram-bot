"""Callback query handlers for Telegram bot."""
import asyncio
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from aiogram import Router, Bot
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import config
from bot.services.youtube import YouTubeService
from bot.services.downloader import DownloadManager
from bot.services.summarizer import SummarizerService
from bot.services.bale_bridge import bale_bridge_service
from bot.services.file_cache import file_cache_service
from bot.services.secure_package import create_secure_zip, DEFAULT_PASSWORD
from bot.services.local_media_registry import remember as remember_local_media
from bot.services.github_apps import github_apps_service
from bot.utils.url_shortener import reconstruct_url, shorten_callback

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()
download_manager = DownloadManager.get_instance()
summarizer = SummarizerService()
_ACTIVE_SPLIT_JOBS: set[str] = set()
# Temporary stability mode: zip-part split is primary until clip splitter is fully stabilized.
ENABLE_VIDEO_CLIP_SPLIT = False


def _canonical_video_ref(url: str) -> str:
    """Normalize YouTube URL to stable cache key based on video id when possible."""
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        if "youtu.be" in host:
            vid = p.path.strip("/")
            if vid:
                return f"youtube:{vid}"
        if "youtube.com" in host:
            qs = parse_qs(p.query or "")
            vid = (qs.get("v") or [""])[0]
            if vid:
                return f"youtube:{vid}"
    except Exception:
        pass
    return url.strip()


async def _get_user_settings(user_id: int) -> dict:
    from bot.database.models import Database
    db = Database()
    await db.init()
    return await db.get_user_settings(user_id)


async def _show_settings(callback: CallbackQuery, user_id: int):
    settings = await _get_user_settings(user_id)
    mode = settings.get("bale_mode", "auto")
    enc = bool(settings.get("bale_encrypt", 1))
    comp = settings.get("compression_level", "medium")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"Bale: {'🟢 Auto' if mode=='auto' else '🟡 Manual'}", callback_data="settings_toggle_mode"),
            InlineKeyboardButton(text=f"Encrypt: {'🔐 ON' if enc else '🔓 OFF'}", callback_data="settings_toggle_encrypt"),
        ],
        [InlineKeyboardButton(text=f"Compression: {comp}", callback_data="settings_cycle_compression")],
    ])
    await callback.message.edit_text("⚙️ Settings updated.", reply_markup=kb)


async def _split_video_for_bale(file_path: Path, part_size_mb: int) -> list[Path]:
    """Split oversized MP4 into playable MP4 clips with controlled part count and size."""
    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg/ffprobe command not found on server")

    safe_part_mb = max(5, int(part_size_mb))
    max_part_bytes = safe_part_mb * 1024 * 1024
    target_bytes = max(4 * 1024 * 1024, int((safe_part_mb - 2) * 1024 * 1024))
    file_size = file_path.stat().st_size

    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await probe.communicate()
    try:
        duration = float((out or b"0").decode().strip() or "0")
    except Exception:
        duration = 0.0

    if duration <= 0:
        raise RuntimeError("could not detect video duration for splitting")

    # Start from expected part count based on size; increase only if needed.
    parts = max(1, int((file_size + target_bytes - 1) // target_bytes))

    for _ in range(6):
        if parts > 20:
            raise RuntimeError("required part count is too high for stable clip split")
        for p in file_path.parent.glob(f"{file_path.stem}.clip*.mp4"):
            p.unlink(missing_ok=True)

        seg_seconds = max(3, int((duration / parts) + 0.999))

        produced: list[Path] = []
        ok = True
        for idx in range(parts):
            start = idx * seg_seconds
            if start >= duration:
                break
            clip_path = file_path.parent / f"{file_path.stem}.clip{idx+1:03d}.mp4"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-ss", str(start), "-t", str(seg_seconds), "-i", str(file_path),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                str(clip_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0 or not clip_path.exists():
                ok = False
                logger.warning("ffmpeg clip split failed: %s", err.decode(errors="ignore")[:200])
                break
            produced.append(clip_path)

        if not ok or not produced:
            for c in produced:
                c.unlink(missing_ok=True)
            raise RuntimeError("video clip split failed")

        too_big_sizes = [c.stat().st_size for c in produced if c.stat().st_size > max_part_bytes]
        if not too_big_sizes:
            return produced

        for c in produced:
            c.unlink(missing_ok=True)
        # Increase part count based on worst overflow ratio.
        worst = max(too_big_sizes)
        scale = max(1, int((worst + max_part_bytes - 1) // max_part_bytes))
        parts = max(parts + 1, parts * scale)

    raise RuntimeError("could not make upload-safe video clips within retry budget")


async def _split_zip_for_bale(file_path: Path, part_size_mb: int) -> list[Path]:
    """Create upload-safe chunks: split raw file, then zip each chunk separately."""
    if shutil.which("split") is None or shutil.which("zip") is None:
        raise RuntimeError("split/zip command not found on server")

    safe_part_mb = max(5, int(part_size_mb))
    # keep headroom for multipart/form-data overhead and Bale quirks
    chunk_mb = max(4, safe_part_mb - 2)
    chunk_prefix = file_path.parent / f"{file_path.stem}.bchunk."

    # cleanup leftovers
    for p in file_path.parent.glob(f"{file_path.stem}.bchunk.*"):
        p.unlink(missing_ok=True)
    for p in file_path.parent.glob(f"{file_path.stem}.bale.part*.zip"):
        p.unlink(missing_ok=True)

    split_proc = await asyncio.create_subprocess_exec(
        "split", "-b", f"{chunk_mb}m", "-d", "-a", "3", str(file_path), str(chunk_prefix),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, split_err = await split_proc.communicate()
    if split_proc.returncode != 0:
        raise RuntimeError(split_err.decode(errors="ignore")[:300] or "split failed")

    chunks = sorted(file_path.parent.glob(f"{file_path.stem}.bchunk.*"))
    if not chunks:
        raise RuntimeError("raw chunks not created")

    zip_parts: list[Path] = []
    ext = file_path.suffix or ".bin"
    for idx, chunk in enumerate(chunks, start=1):
        chunk_named = file_path.parent / f"{file_path.stem}.part{idx:03d}{ext}"
        chunk_named.unlink(missing_ok=True)
        chunk.rename(chunk_named)

        zip_path = file_path.parent / f"{file_path.stem}.bale.part{idx:03d}.zip"
        zip_proc = await asyncio.create_subprocess_exec(
            "zip", "-j", "-0", str(zip_path), str(chunk_named),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, zip_err = await zip_proc.communicate()
        if zip_proc.returncode != 0 or not zip_path.exists():
            raise RuntimeError(zip_err.decode(errors="ignore")[:300] or "zip chunk failed")
        zip_parts.append(zip_path)
        chunk_named.unlink(missing_ok=True)

    for c in chunks:
        c.unlink(missing_ok=True)

    return zip_parts


async def _send_or_offer_bale(user_id: int, bot: Bot, local_path: str, media_type: str, force_send: bool = False):
    settings = await _get_user_settings(user_id)
    mode = settings.get("bale_mode", "auto")
    if int(settings.get("sos_mode", 0)) == 1:
        mode = "auto"

    work_path = Path(local_path)
    slim_tmp = None
    secure_tmp = None

    # Safe-size path for Bale delivery
    bale_max_bytes = config.BALE_SAFE_MAX_MB * 1024 * 1024
    if media_type == "audio" and work_path.exists() and work_path.stat().st_size > bale_max_bytes:
        await bot.send_message(user_id, f"ℹ️ فایل برای بله بزرگ بود ({work_path.stat().st_size / (1024*1024):.1f}MB). نسخه سبک‌تر می‌سازم...")
        from bot.services.audio_compressor import AudioCompressionService
        ac = AudioCompressionService()
        slim = await ac.compress_audio(work_path, target_bitrate_k=32, sample_rate=22050, channels=1)
        if slim:
            work_path = slim
            slim_tmp = slim

    if mode == "manual" and not force_send:
        key = file_cache_service.make_key("bale-manual", str(user_id), media_type, str(work_path))
        cached = file_cache_service.put(key, work_path)
        if cached:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 ارسال به بله", callback_data=f"bale_send|{key}|{media_type}")]])
            await bot.send_message(user_id, "ارسال به بله در حالت دستی است.", reply_markup=kb)
        if slim_tmp and slim_tmp.exists() and not file_cache_service.is_cache_file(slim_tmp):
            slim_tmp.unlink(missing_ok=True)
        return

    bale_send_path = work_path
    if bool(settings.get("bale_encrypt", 1)):
        pwd = settings.get("bale_password") or DEFAULT_PASSWORD
        zipped = await create_secure_zip(work_path, settings.get("compression_level", "medium"), pwd)
        if zipped:
            bale_send_path = zipped
            secure_tmp = zipped

    split_parts: list[Path] = []
    split_media_type = "document"
    bale_target = Path(bale_send_path)
    bale_max_bytes = config.BALE_SAFE_MAX_MB * 1024 * 1024

    if bale_target.exists() and bale_target.stat().st_size > bale_max_bytes:
        split_job_key = f"{user_id}:{str(bale_target.resolve())}"
        if split_job_key in _ACTIVE_SPLIT_JOBS:
            await bot.send_message(user_id, "⏳ همین فایل الان در حال تقسیم/ارسال به بله است. لطفاً صبر کن.")
            if slim_tmp and slim_tmp.exists() and not file_cache_service.is_cache_file(slim_tmp):
                slim_tmp.unlink(missing_ok=True)
            if secure_tmp and secure_tmp.exists() and not file_cache_service.is_cache_file(secure_tmp):
                secure_tmp.unlink(missing_ok=True)
            return

        _ACTIVE_SPLIT_JOBS.add(split_job_key)
        try:
            try:
                if ENABLE_VIDEO_CLIP_SPLIT and media_type == "video" and bale_target.suffix.lower() == ".mp4":
                    await bot.send_message(
                        user_id,
                        f"ℹ️ فایل بزرگه ({bale_target.stat().st_size / (1024*1024):.1f}MB). در حال تقسیم به کلیپ‌های MP4..."
                    )
                    split_parts = await _split_video_for_bale(bale_target, config.BALE_SAFE_MAX_MB)
                    split_media_type = "video"
                else:
                    await bot.send_message(
                        user_id,
                        f"ℹ️ فایل از سقف آپلود بله بزرگ‌تره ({bale_target.stat().st_size / (1024*1024):.1f}MB). در حال تبدیل به zip چندپارته..."
                    )
                    split_parts = await _split_zip_for_bale(bale_target, config.BALE_SAFE_MAX_MB)
                    split_media_type = "document"
            except Exception:
                logger.exception("Failed to split file for Bale")
                # Fallback: zip multi-part when video clip split is not possible
                try:
                    split_parts = await _split_zip_for_bale(bale_target, config.BALE_SAFE_MAX_MB)
                    split_media_type = "document"
                    await bot.send_message(user_id, "ℹ️ تقسیم ویدیویی موفق نشد؛ با روش zip چندپارته ارسال می‌کنم.")
                except Exception as e2:
                    logger.exception("Fallback zip split also failed")
                    await bot.send_message(user_id, f"⚠️ تقسیم فایل برای بله ناموفق بود: {str(e2)[:140]}")
        finally:
            _ACTIVE_SPLIT_JOBS.discard(split_job_key)

    if split_parts:
        total = len(split_parts)
        bale_ok = True
        for i, part in enumerate(split_parts, start=1):
            cap = f"Night bridge part {i}/{total}"
            ok = await bale_bridge_service.forward_file(part, split_media_type, caption=cap)
            if not ok:
                bale_ok = False
                break
    else:
        media_for_send = "document" if str(bale_send_path).endswith(".zip") else media_type
        bale_ok = await bale_bridge_service.forward_file(bale_send_path, media_for_send, caption="Night bridge")

    await bot.send_message(user_id, "✅ فایل به بله ارسال شد." if bale_ok else "⚠️ ارسال فایل به بله ناموفق بود.")

    if bale_ok and split_parts and split_media_type == "document":
        ext = bale_target.suffix or ".bin"
        restore_name = f"restored{ext}"
        await bot.send_message(
            user_id,
            "🧩 راهنمای اتصال پارت‌ها بعد از دانلود از بله:\n"
            "1) همه zipها را در یک پوشه Extract کن.\n"
            f"2) داخل همان پوشه CMD باز کن و بزن:\ncopy /b *.part* {restore_name}\n"
            "3) فایل خروجی را اجرا کن. (اگر ویدیو بود، پسوند mp4 دارد)"
        )

    if slim_tmp and slim_tmp.exists() and not file_cache_service.is_cache_file(slim_tmp):
        slim_tmp.unlink(missing_ok=True)
    if secure_tmp and secure_tmp.exists() and not file_cache_service.is_cache_file(secure_tmp):
        secure_tmp.unlink(missing_ok=True)
    for p in split_parts:
        if p.exists() and not file_cache_service.is_cache_file(p):
            p.unlink(missing_ok=True)


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
    
    if action == "settings_toggle_mode":
        from bot.database.models import Database
        db = Database(); await db.init()
        s = await db.get_user_settings(callback.from_user.id)
        next_mode = "manual" if s.get("bale_mode", "auto") == "auto" else "auto"
        await db.update_user_settings(callback.from_user.id, bale_mode=next_mode)
        await _show_settings(callback, callback.from_user.id)
        await callback.answer("Bale mode updated")
        return

    if action == "settings_toggle_encrypt":
        from bot.database.models import Database
        db = Database(); await db.init()
        s = await db.get_user_settings(callback.from_user.id)
        next_val = 0 if int(s.get("bale_encrypt", 1)) else 1
        await db.update_user_settings(callback.from_user.id, bale_encrypt=next_val)
        await _show_settings(callback, callback.from_user.id)
        await callback.answer("Encryption updated")
        return

    if action == "settings_cycle_compression":
        from bot.database.models import Database
        db = Database(); await db.init()
        s = await db.get_user_settings(callback.from_user.id)
        cur = s.get("compression_level", "medium")
        levels = ["low", "medium", "high"]
        next_level = levels[(levels.index(cur) + 1) % len(levels)] if cur in levels else "medium"
        await db.update_user_settings(callback.from_user.id, compression_level=next_level)
        await _show_settings(callback, callback.from_user.id)
        await callback.answer("Compression profile updated")
        return

    if action == "bale_send":
        key = parts[1] if len(parts) > 1 else ""
        media = parts[2] if len(parts) > 2 else "document"
        local = file_cache_service.get(key)
        if not local:
            await callback.answer("فایل منقضی شده یا پیدا نشد", show_alert=True)
            return
        await _send_or_offer_bale(callback.from_user.id, bot, local, media, force_send=True)
        await callback.answer("در حال ارسال به بله...")
        return

    if action == "ghdl":
        full_name = parts[1] if len(parts) > 1 else ""
        if not full_name or "/" not in full_name:
            await callback.answer("Repo نامعتبر", show_alert=True)
            return

        user_id = callback.from_user.id
        status = await bot.send_message(user_id, f"📦 بررسی release برای <b>{full_name}</b> ...")
        release = await github_apps_service.latest_release_assets(full_name)
        assets = release.get("assets", [])
        tag = release.get("tag", "")
        if not assets:
            await status.edit_text("❌ Latest release یا asset پیدا نشد.")
            return

        picks = github_apps_service.pick_target_assets(assets)
        sent = 0
        await status.edit_text(f"📦 Release: <b>{tag or 'latest'}</b>\nدر حال دانلود assetها...")
        for label, asset in (("android_v8a", picks.get("android_v8a")), ("windows_x64", picks.get("windows_x64"))):
            if not asset:
                continue
            local = await github_apps_service.download_asset(asset["url"], asset["name"])
            if not local:
                continue

            size_h = asset.get("size_h") or "?"
            doc = await bot.send_document(user_id, FSInputFile(str(local)), caption=f"{full_name}\n{label} • {size_h}\n🏷 {tag or 'latest'}")
            try:
                if getattr(doc, "document", None):
                    remember_local_media(user_id, doc.document.file_id, str(local))
            except Exception:
                pass

            if bale_bridge_service.enabled:
                await _send_or_offer_bale(user_id, bot, str(local), "document")
            sent += 1

        if sent == 0:
            await status.edit_text("⚠️ فایل مناسب پیدا نشد (v8a یا windows x64).")
        else:
            await status.edit_text(f"✅ {sent} فایل ارسال شد (تلگرام + بله).")
        await callback.answer("Done")
        return

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
        compress = parts[3] if len(parts) > 3 else "0"
        
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
        await handle_video_download_inline(callback, url, format_id, compress == "1", bot)
    elif action == "dl_audio":
        compress = parts[3] if len(parts) > 3 else "0"
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="⏳ Extracting audio..."
        )
        await handle_audio_download_inline(callback, url, compress == "1", bot)
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
        success = await upload_with_retry(bot, callback.message, "video", path, user_id=callback.from_user.id)
        
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
        success = await upload_with_retry(bot, callback.message, "audio", path, user_id=callback.from_user.id)
        
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
        success = await upload_with_retry(bot, callback.message, "video", path, user_id=callback.from_user.id)
        if success:
            await callback.message.answer("✅ Live stream captured and uploaded!")
        else:
            await callback.message.answer("❌ Upload failed. File retained.")
            return
    else:
        await callback.message.answer("❌ Live capture failed.")
    
    await status_msg.delete()


async def upload_with_retry(bot: Bot, message, media_type: str, path: str, user_id: int | None = None, max_retries: int = 3):
    """Upload file to Telegram with retry, then optionally forward to Bale."""
    import os

    for attempt in range(max_retries):
        try:
            sent_msg = None
            if media_type == "video":
                sent_msg = await message.answer_video(FSInputFile(path))
            elif media_type == "audio":
                sent_msg = await message.answer_audio(FSInputFile(path))

            if user_id and sent_msg:
                try:
                    if getattr(sent_msg, "video", None):
                        remember_local_media(user_id, sent_msg.video.file_id, path)
                    elif getattr(sent_msg, "audio", None):
                        remember_local_media(user_id, sent_msg.audio.file_id, path)
                    elif getattr(sent_msg, "document", None):
                        remember_local_media(user_id, sent_msg.document.file_id, path)
                except Exception:
                    pass

            # Forward to Bale / offer manual send based on user settings
            if bale_bridge_service.enabled and user_id:
                bale_media_type = media_type if media_type in {"video", "audio"} else "document"
                await _send_or_offer_bale(user_id, bot, path, bale_media_type)

            # Only remove non-cached temp files after Telegram upload completes
            if os.path.exists(path) and not file_cache_service.is_cache_file(path):
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

async def handle_video_download_inline(callback: CallbackQuery, url: str, format_id: str, compress: bool, bot: Bot):
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
    
    cache_ref = _canonical_video_ref(url)
    base_cache_key = file_cache_service.make_key("yt", "video", cache_ref, format_id)
    compressed_cache_key = file_cache_service.make_key("yt", "video", cache_ref, format_id, "compressed")

    path = file_cache_service.get(compressed_cache_key if compress else base_cache_key)
    if path:
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=status_msg.message_id,
                text=f"🎬 <b>{title}</b>\n\n♻️ از نسخه کش‌شده استفاده شد\n📤 Uploading..."
            )
        except Exception:
            pass
    else:
        task_id = download_manager.add_download(
            user_id=user_id,
            url=url,
            format_id=format_id,
            mode="video",
            progress_callback=progress_callback
        )
        path = await download_manager.wait_for_download(task_id)

    if path:
        final_path = path

        # Compress if requested
        if compress and not file_cache_service.get(compressed_cache_key):
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=status_msg.message_id,
                    text=f"🎬 <b>{title}</b>\n\n"
                         f"🗜 Status: <b>Compressing...</b>\n"
                         f"⏳ Please wait..."
                )
            except:
                pass
            
            from bot.services.compressor import CompressionService
            from pathlib import Path
            compressor = CompressionService()
            compressed_path = await compressor.compress_video(Path(path))
            
            if compressed_path:
                import os
                original_mb = os.path.getsize(path) / (1024 * 1024)
                compressed_mb = os.path.getsize(compressed_path) / (1024 * 1024)

                # Cache compressed output and keep original if cached
                cached_compressed = file_cache_service.put(compressed_cache_key, compressed_path)
                if not file_cache_service.is_cache_file(path) and os.path.exists(path):
                    os.remove(path)
                if compressed_path.exists() and not file_cache_service.is_cache_file(compressed_path):
                    compressed_path.unlink(missing_ok=True)
                final_path = cached_compressed or str(compressed_path)

                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=status_msg.message_id,
                        text=f"🎬 <b>{title}</b>\n\n"
                             f"✅ Compressed: {original_mb:.1f}MB → {compressed_mb:.1f}MB\n"
                             f"📤 Uploading..."
                    )
                except:
                    pass
        
        # Cache non-compressed download for reuse
        if not compress and not file_cache_service.is_cache_file(final_path):
            cached_path = file_cache_service.put(base_cache_key, final_path)
            if cached_path:
                final_path = cached_path

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
        
        success = await upload_with_retry(bot, status_msg, "video", final_path, user_id=user_id)
        
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


async def handle_audio_download_inline(callback: CallbackQuery, url: str, compress: bool, bot: Bot):
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
    
    cache_ref = _canonical_video_ref(url)
    base_cache_key = file_cache_service.make_key("yt", "audio", cache_ref, "bestaudio")
    compressed_cache_key = file_cache_service.make_key("yt", "audio", cache_ref, "bestaudio", "compressed-64k")

    path = file_cache_service.get(compressed_cache_key if compress else base_cache_key)
    if path:
        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=status_msg.message_id,
                text="🎵 ♻️ از نسخه کش‌شده استفاده شد\n📤 Uploading..."
            )
        except Exception:
            pass
    else:
        task_id = download_manager.add_download(
            user_id=user_id,
            url=url,
            format_id="bestaudio",
            mode="audio",
            progress_callback=progress_callback
        )
        path = await download_manager.wait_for_download(task_id)

    if path:
        from pathlib import Path
        final_path = path
        
        # Compress if requested
        if compress and not file_cache_service.get(compressed_cache_key):
            try:
                await bot.edit_message_text(
                    chat_id=user_id,
                    message_id=status_msg.message_id,
                    text=f"🎵 <b>Compressing audio...</b>\n⏳ Please wait..."
                )
            except:
                pass
            
            from bot.services.audio_compressor import AudioCompressionService
            settings = await _get_user_settings(user_id)
            bitrate_map = {"low": 48, "medium": 64, "high": 96}
            bitrate = bitrate_map.get(settings.get("compression_level", "medium"), 64)
            audio_compressor = AudioCompressionService()
            compressed_path = await audio_compressor.compress_audio(Path(path), target_bitrate_k=bitrate)
            
            if compressed_path:
                import os
                original_mb = os.path.getsize(path) / (1024 * 1024)
                compressed_mb = os.path.getsize(compressed_path) / (1024 * 1024)

                cached_compressed = file_cache_service.put(compressed_cache_key, compressed_path)
                if not file_cache_service.is_cache_file(path) and os.path.exists(path):
                    os.remove(path)
                if compressed_path.exists() and not file_cache_service.is_cache_file(compressed_path):
                    compressed_path.unlink(missing_ok=True)
                final_path = cached_compressed or str(compressed_path)

                try:
                    await bot.edit_message_text(
                        chat_id=user_id,
                        message_id=status_msg.message_id,
                        text=f"🎵 <b>Compressed</b>\n"
                             f"{original_mb:.1f}MB → {compressed_mb:.1f}MB\n"
                             f"📤 Uploading..."
                    )
                except:
                    pass
        
        # Cache non-compressed output for reuse
        if not compress and not file_cache_service.is_cache_file(final_path):
            cached_path = file_cache_service.put(base_cache_key, final_path)
            if cached_path:
                final_path = cached_path

        # Get audio metadata before upload
        from bot.services.audio_compressor import AudioCompressionService
        audio_comp = AudioCompressionService()
        metadata = await audio_comp.get_audio_metadata(Path(final_path))
        
        duration_sec = metadata.get("duration", 0)
        size_mb = metadata.get("size_mb", 0)
        
        # Upload with metadata caption
        try:
            caption = f"🎵 Audio\n"
            if duration_sec > 0:
                mins = duration_sec // 60
                secs = duration_sec % 60
                caption += f"⏱ {mins}:{secs:02d}\n"
            caption += f"💾 {size_mb:.1f} MB"
            if compress:
                caption += " (Compressed)"
            
            sent_msg = await bot.send_audio(
                chat_id=user_id,
                audio=FSInputFile(final_path),
                caption=caption,
                duration=duration_sec if duration_sec > 0 else None
            )
            try:
                if getattr(sent_msg, "audio", None):
                    remember_local_media(user_id, sent_msg.audio.file_id, final_path)
            except Exception:
                pass

            if bale_bridge_service.enabled:
                await _send_or_offer_bale(user_id, bot, final_path, "audio")

            import os
            if os.path.exists(final_path) and not file_cache_service.is_cache_file(final_path):
                os.remove(final_path)

            # Save to history
            from bot.database.models import Database
            db = Database()
            await db.init()
            info = await youtube_service.get_video_info(url)
            await db.add_download(
                user_id=user_id,
                url=url,
                title=info.get("title", "Unknown") if info else "Audio",
                status="completed",
                file_path=final_path
            )
        except Exception as e:
            logger.error(f"Audio upload error: {e}")
            await bot.send_message(user_id, f"❌ Upload failed: {e}")
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
        success = await upload_with_retry(bot, status_msg, "video", path, user_id=user_id)
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
