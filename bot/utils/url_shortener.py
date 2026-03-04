"""URL shortening for Telegram callback_data (64 byte limit)."""
import re
from typing import Optional


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'^([0-9A-Za-z_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def reconstruct_url(video_id: str) -> str:
    """Reconstruct YouTube URL from video ID."""
    return f"https://www.youtube.com/watch?v={video_id}"


def shorten_callback(action: str, url: str, *args) -> str:
    """Create short callback_data using video ID instead of full URL."""
    video_id = extract_video_id(url)
    if not video_id:
        # Fallback: use URL but truncate if needed
        parts = [action, url[:30]] + list(args)
        return "|".join(parts)[:64]
    
    parts = [action, video_id] + list(args)
    return "|".join(parts)
