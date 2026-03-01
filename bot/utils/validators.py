"""URL and input validation utilities."""
import re
from typing import Optional


# YouTube URL patterns
YOUTUBE_PATTERNS = [
    # Standard video
    r"(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+",
    # Short video
    r"(https?://)?(www\.)?youtu\.be/[\w-]+",
    # Channel links
    r"(https?://)?(www\.)?youtube\.com/(c|channel|@)[\w-]+",
    # Playlist
    r"(https?://)?(www\.)?youtube\.com/playlist\?list=[\w-]+",
    # Live streams
    r"(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+.*(&|\?)(live=1|live)",
]


def validate_youtube_url(url: str) -> bool:
    """Validate if URL is a valid YouTube URL."""
    if not url:
        return False
    
    url = url.strip().lower()
    
    for pattern in YOUTUBE_PATTERNS:
        if re.match(pattern, url, re.IGNORECASE):
            return True
    
    return False


def is_channel_url(url: str) -> bool:
    """Check if URL is a YouTube channel URL."""
    url = url.strip().lower()
    
    channel_patterns = [
        r"(https?://)?(www\.)?youtube\.com/c/[\w-]+",
        r"(https?://)?(www\.)?youtube\.com/channel/[\w-]+",
        r"(https?://)?(www\.)?youtube\.com/@[\w-]+",
    ]
    
    for pattern in channel_patterns:
        if re.match(pattern, url):
            return True
    
    return False


def is_playlist_url(url: str) -> bool:
    """Check if URL is a YouTube playlist URL."""
    url = url.strip().lower()
    
    if "playlist" in url and "list=" in url:
        return True
    
    return False


def is_live_url(url: str) -> bool:
    """Check if URL is for a live stream."""
    url = url.strip().lower()
    
    if "live=1" in url or url.endswith("/live"):
        return True
    
    return False


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters."""
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    return filename.strip()
