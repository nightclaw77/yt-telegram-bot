"""YouTube service for video info, search, and channel data."""
import asyncio
import json
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class YouTubeService:
    """Service for YouTube operations using yt-dlp."""
    
    async def get_video_info(self, url: str) -> Optional[Dict]:
        """Get video information from URL."""
        cmd = ["yt-dlp", "--dump-json", "--no-playlist", url]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                data = json.loads(stdout.decode())
                return {
                    "id": data.get("id"),
                    "title": data.get("title", "Unknown"),
                    "duration": data.get("duration", 0),
                    "uploader": data.get("uploader", "Unknown"),
                    "is_live": data.get("is_live", False),
                    "thumbnail": data.get("thumbnail"),
                    "view_count": data.get("view_count", 0),
                }
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
        return None
    
    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search YouTube for videos."""
        cmd = ["yt-dlp", f"ytsearch{limit}:{query}", "--dump-json", "--flat-playlist"]
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
                results.append({
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "url": f"https://www.youtube.com/watch?v={data.get('id')}"
                })
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
            "yt-dlp", 
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
            cmd = ["yt-dlp", "--dump-json", "--no-playlist", target]
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
        cmd = ["yt-dlp", "--dump-json", "--no-playlist", "--list-formats", url]
        
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
