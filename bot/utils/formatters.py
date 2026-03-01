"""Message formatting utilities."""
from datetime import datetime
from typing import Optional, Dict, List


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string."""
    if not seconds:
        return "N/A"
    
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_file_size(bytes_size: int) -> str:
    """Format file size in bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} PB"


def format_view_count(count: int) -> str:
    """Format view count with commas."""
    if not count:
        return "0"
    return f"{count:,}"


def format_progress_bar(percent: float, width: int = 20) -> str:
    """Create a text-based progress bar."""
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent:.1f}%"


def format_video_info(info: Dict) -> str:
    """Format video info as a string."""
    title = info.get("title", "Unknown")
    uploader = info.get("uploader", "Unknown")
    duration = format_duration(info.get("duration", 0))
    views = format_view_count(info.get("view_count", 0))
    
    text = f"🎬 <b>{title}</b>\n"
    text += f"👤 {uploader}\n"
    text += f"👁️ {views} views\n"
    text += f"⏱️ {duration}\n"
    
    if info.get("is_live"):
        text += "🔴 LIVE\n"
    
    return text


def format_download_status(status: str, progress: Optional[float] = None) -> str:
    """Format download status message."""
    status_messages = {
        "pending": "⏳ Waiting in queue...",
        "downloading": f"📥 Downloading... {format_progress_bar(progress or 0)}",
        "completed": "✅ Download complete!",
        "failed": "❌ Download failed",
        "cancelled": "🚫 Download cancelled"
    }
    
    return status_messages.get(status, status)


def format_search_results(results: List[Dict]) -> str:
    """Format search results as a string."""
    if not results:
        return "❌ No results found."
    
    text = "🔍 <b>Search Results:</b>\n\n"
    
    for i, result in enumerate(results, 1):
        title = result.get("title", "Unknown")
        text += f"{i}. {title}\n"
    
    return text


def format_channel_overview(
    latest: List[Dict],
    top: List[Dict],
    live: Optional[List[Dict]] = None
) -> str:
    """Format channel overview."""
    text = "🏢 <b>Channel Overview</b>\n\n"
    
    if live:
        text += "🔴 <b>LIVE NOW:</b>\n"
        for v in live:
            text += f"• {v['title']}\n"
        text += "\n"
    
    if latest:
        text += "🆕 <b>Latest Uploads:</b>\n"
        for i, v in enumerate(latest, 1):
            text += f"{i}. {v['title']}\n"
        text += "\n"
    
    if top:
        text += "🔥 <b>Top Videos:</b>\n"
        for i, v in enumerate(top, 1):
            text += f"{i}. {v['title']}\n"
    
    return text


def format_error(error: str) -> str:
    """Format error message."""
    return f"❌ <b>Error:</b> {error}"


def format_success(message: str) -> str:
    """Format success message."""
    return f"✅ {message}"
