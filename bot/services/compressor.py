"""Video compression service using ffmpeg."""
import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from bot.config import config

logger = logging.getLogger(__name__)


class CompressionService:
    """Compress local video files with ffmpeg."""

    def __init__(self):
        self.downloads_dir = config.DOWNLOADS_DIR

    def _ensure_ffmpeg(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    async def _probe_duration(self, path: Path) -> Optional[float]:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        try:
            return float(stdout.decode().strip())
        except Exception:
            return None

    async def compress_video(self, input_path: Path) -> Optional[Path]:
        """Compress video and return output path."""
        if not self._ensure_ffmpeg():
            raise RuntimeError("ffmpeg/ffprobe is not installed on host")

        if not input_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_path}")

        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        output_path = self.downloads_dir / f"cmp_{uuid.uuid4().hex[:10]}.mp4"

        input_size_bytes = input_path.stat().st_size
        duration = await self._probe_duration(input_path)

        # Target roughly 65% of original size when duration is known.
        target_bitrate_k = None
        if duration and duration > 0:
            target_total_bits = input_size_bytes * 8 * 0.65
            target_bitrate_k = max(int((target_total_bits / duration) / 1000), 350)

        cmd = ["ffmpeg", "-y", "-i", str(input_path)]

        if target_bitrate_k:
            video_bitrate = max(target_bitrate_k - 96, 300)
            cmd += [
                "-c:v", "libx264",
                "-b:v", f"{video_bitrate}k",
                "-maxrate", f"{int(video_bitrate * 1.2)}k",
                "-bufsize", f"{int(video_bitrate * 2)}k",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-b:a", "96k",
                "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            # Fallback when metadata missing.
            cmd += [
                "-c:v", "libx264",
                "-crf", "30",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-b:a", "96k",
                "-movflags", "+faststart",
                str(output_path),
            ]

        logger.info("Compressing video: %s", " ".join(cmd))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error("Compression failed: %s", stderr.decode(errors="ignore"))
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            return None

        if not output_path.exists() or output_path.stat().st_size == 0:
            return None

        return output_path
