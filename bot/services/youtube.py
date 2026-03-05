"""YouTube service for video info, search, and channel data."""
import asyncio
import json
import logging
import os
import subprocess
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from subprocess import TimeoutExpired

# Get yt-dlp path - prefer venv version
VENV_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "venv", "bin")
VENV_YTDLP = os.path.join(VENV_BIN, "yt-dlp")
YTDLP_CMD = VENV_YTDLP if os.path.exists(VENV_YTDLP) else "yt-dlp"

logger = logging.getLogger(__name__)


def get_relative_time(upload_date_str: str) -> str:
    """Convert YYYYMMDD to relative time like '2 hours ago', '3 days ago'."""
    if not upload_date_str or len(upload_date_str) != 8:
        return None

    try:
        # Parse date from YYYYMMDD format
        upload_date = datetime.strptime(upload_date_str, "%Y%m%d")
        now = datetime.now()
        delta = now - upload_date

        if delta < timedelta(hours=1):
            minutes = int(delta.total_seconds() / 60)
            if minutes <= 1:
                return "just now"
            return f"{minutes} min ago"
        elif delta < timedelta(days=1):
            hours = int(delta.total_seconds() / 3600)
            if hours == 1:
                return "1 hour ago"
            return f"{hours} hours ago"
        elif delta < timedelta(days=30):
            days = delta.days
            if days == 1:
                return "yesterday"
            return f"{days} days ago"
        elif delta < timedelta(days=365):
            months = delta.days // 30
            if months == 1:
                return "1 month ago"
            return f"{months} months ago"
        else:
            years = delta.days // 365
            if years == 1:
                return "1 year ago"
            return f"{years} years ago"
    except:
        return None

    return None


class YouTubeService:
    """Service for YouTube operations using yt-dlp."""

    async def get_video_info(self, url: str) -> Optional[Dict]:
        """Get video information from URL."""
        cmd = [YTDLP_CMD, "--dump-json", "--no-playlist", url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                # Convert upload_date to relative time
                upload_date_str = data.get("upload_date", "")
                upload_date = get_relative_time(upload_date_str) if upload_date_str else None

                return {
                    "id": data.get("id"),
                    "title": data.get("title", "Unknown"),
                    "duration": data.get("duration", 0),
                    "uploader": data.get("uploader", "Unknown"),
                    "is_live": data.get("is_live", False),
                    "thumbnail": data.get("thumbnail"),
                    "view_count": data.get("view_count", 0),
                    "upload_date": upload_date,
                }
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
        return None

    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search YouTube for videos."""
        cmd = [YTDLP_CMD, f"ytsearch{limit}:{query}", "--dump-json", "--flat-playlist"]
        results = []

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            for line in stdout.decode().splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if not data.get("id") or not data.get("title"):
                    continue
                
                # Get thumbnail - prefer default or medium quality
                thumbnails = data.get("thumbnails", [])
                thumbnail_url = None
                if thumbnails:
                    # Try to get medium quality first
                    for thumb in thumbnails:
                        if thumb.get("url"):
                            thumbnail_url = thumb["url"]
                            # Prefer medium or high quality
                            if thumb.get("height", 0) >= 180:
                                break

                # Format upload date to relative time - only available for individual video info
                # Search results don't have upload_date, so we'll use timestamp if available
                timestamp = data.get("timestamp")
                upload_date = None
                if timestamp:
                    try:
                        upload_date = datetime.fromtimestamp(timestamp)
                        now = datetime.now()
                        delta = now - upload_date
                        if delta < timedelta(hours=1):
                            minutes = int(delta.total_seconds() / 60)
                            upload_date = f"{minutes} min ago" if minutes > 1 else "just now"
                        elif delta < timedelta(days=1):
                            hours = int(delta.total_seconds() / 3600)
                            upload_date = f"{hours} hour{'s' if hours > 1 else ''} ago"
                        elif delta < timedelta(days=30):
                            days = delta.days
                            upload_date = f"{days} day{'s' if days > 1 else ''} ago"
                        elif delta < timedelta(days=365):
                            months = delta.days // 30
                            upload_date = f"{months} month{'s' if months > 1 else ''} ago"
                        else:
                            years = delta.days // 365
                            upload_date = f"{years} year{'s' if years > 1 else ''} ago"
                    except:
                        upload_date = None

                results.append({
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "url": f"https://www.youtube.com/watch?v={data.get('id')}",
                    "thumbnail": thumbnail_url or data.get("thumbnail"),
                    "view_count": data.get("view_count", 0),
                    "duration": data.get("duration", 0),
                    "uploader": data.get("uploader", data.get("channel", "Unknown")),
                    "channel_id": data.get("channel_id", ""),
                    "upload_date": upload_date
                })

            # Enrich top results with relative upload date (best effort)
            for item in results[: min(5, len(results))]:
                if item.get("upload_date"):
                    continue
                info = await self.get_video_info(item.get("url", ""))
                if info and info.get("upload_date"):
                    item["upload_date"] = info.get("upload_date")
        except Exception as e:
            logger.error(f"Search error: {e}")

        return results

    async def get_channel_videos(
        self,
        channel_url: str,
        mode: str = "latest",
        limit: int = 3
    ) -> List[Dict]:
        """Get channel videos (latest, top, or live)."""
        # mode: latest, top, live
        cmd = [
            YTDLP_CMD,
            "--dump-json",
            "--flat-playlist",
            "--playlist-end",
            str(limit)
        ]

        if mode == "top":
            cmd.extend(["--order", "relevance"])

        if mode == "live":
            # Specific live stream probe
            target = channel_url.rstrip("/") + "/live"
            cmd = [YTDLP_CMD, "--dump-json", "--no-playlist", target]
        else:
            cmd.append(channel_url)

        videos = []
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            for line in stdout.decode().splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                videos.append({
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "url": data.get("url", f"https://www.youtube.com/watch?v={data.get('id')}")
                })
        except Exception as e:
            logger.error(f"Channel fetch error ({mode}): {e}")

        return videos

    async def get_formats(self, url: str) -> List[Dict]:
        """Get available formats for a video."""
        cmd = [YTDLP_CMD, "--dump-json", "--no-playlist", "--list-formats", url]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                data = json.loads(stdout.decode())
                return data.get("formats", [])
        except Exception as e:
            logger.error(f"Error getting formats: {e}")

        return []
