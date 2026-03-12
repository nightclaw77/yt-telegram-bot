"""Audio compression service using ffmpeg."""
import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from bot.config import config

logger = logging.getLogger(__name__)


class AudioCompressionService:
    """Compress audio files with ffmpeg."""

    def __init__(self):
        self.downloads_dir = config.DOWNLOADS_DIR

    def _ensure_ffmpeg(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    async def compress_audio(
        self,
        input_path: Path,
        target_bitrate_k: int = 64,
        sample_rate: int = 44100,
        channels: int = 2,
    ) -> Optional[Path]:
        """Compress audio file and return output path only if it is meaningfully smaller."""
        if not self._ensure_ffmpeg():
            raise RuntimeError("ffmpeg/ffprobe is not installed")

        if not input_path.exists():
            raise FileNotFoundError(f"Input audio not found: {input_path}")

        source_meta = await self.get_audio_metadata(input_path)
        source_bitrate = source_meta.get("bitrate_kbps") or 0
        source_size = input_path.stat().st_size

        # Skip useless recompression when source is already at/below target bitrate.
        if source_bitrate and source_bitrate <= target_bitrate_k + 8:
            logger.info(
                "Skipping compression for %s: source bitrate %.1f kbps already <= target %sk",
                input_path,
                source_bitrate,
                target_bitrate_k,
            )
            return None

        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.downloads_dir / f"cmp_audio_{uuid.uuid4().hex[:10]}.mp3"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-map", "a:0",
            "-vn",
            "-c:a", "libmp3lame",
            "-compression_level", "2",
            "-b:a", f"{target_bitrate_k}k",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            str(output_path)
        ]

        logger.info("Compressing audio: %s", " ".join(cmd))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error("Audio compression failed: %s", stderr.decode(errors="ignore"))
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            return None

        if not output_path.exists() or output_path.stat().st_size == 0:
            return None

        compressed_size = output_path.stat().st_size

        # Reject bogus "compression" that increases size or barely helps.
        if compressed_size >= source_size or compressed_size > source_size * 0.97:
            logger.info(
                "Discarding compressed output for %s: original=%s compressed=%s",
                input_path,
                source_size,
                compressed_size,
            )
            output_path.unlink(missing_ok=True)
            return None

        return output_path

    async def get_audio_metadata(self, path: Path) -> dict:
        """Extract audio duration and bitrate."""
        if not self._ensure_ffmpeg():
            return {}

        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,bit_rate,size",
            "-of", "json",
            str(path)
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()

        if process.returncode != 0:
            return {}

        try:
            data = json.loads(stdout.decode())
            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0))
            size_bytes = int(fmt.get("size", 0))
            
            return {
                "duration": int(duration),
                "size_mb": size_bytes / (1024 * 1024),
                "bitrate_kbps": int(fmt.get("bit_rate", 0)) / 1000 if fmt.get("bit_rate") else None
            }
        except Exception as e:
            logger.warning(f"Failed to parse audio metadata: {e}")
            return {}
