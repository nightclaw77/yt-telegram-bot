"""Inline query handler for searching videos and channels."""
import logging
import hashlib
import asyncio
from typing import List

from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedPhoto, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from bot.services.youtube import YouTubeService

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()

def format_view_count(views: int) -> str:
    """Format view count to human readable."""
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    elif views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)

def format_duration(seconds: int) -> str:
    """Format duration to mm:ss or hh:mm:ss."""
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"

@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """Handle inline query for YouTube search."""
    query = inline_query.query.strip()
    if not query:
        # Return a welcome message when no query
        await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    id="welcome",
                    title="🎬 Welcome to Night YouTube Bot",
                    input_message_content=InputTextMessageContent(
                        message_text="🎬 <b>Night YouTube Bot</b>\n\nType a search query or paste a YouTube URL to get started!",
                        parse_mode=ParseMode.HTML
                    ),
                    description="Search YouTube videos inline"
                )
            ],
            cache_time=60
        )
        return

    logger.info(f"Inline search query: {query}")
    
    try:
        # Check if it's a channel URL or search keywords
        if "youtube.com" in query or "youtu.be" in query:
            # If it's a URL, we'll try to get video info
            info = await youtube_service.get_video_info(query)
            if info:
                results = [create_inline_result(info)]
            else:
                # Maybe it's a channel?
                channel_videos = await youtube_service.get_channel_videos(query, limit=5)
                results = [create_inline_result_from_search(v) for v in channel_videos]
        else:
            # Search for videos
            search_results = await youtube_service.search(query, limit=10)
            results = [create_inline_result_from_search(v) for v in search_results]

        logger.info(f"Found {len(results)} results for query: {query}")
        
        # If no results, show a message
        if not results:
            results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ No results found",
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ No results found for: <b>{query}</b>",
                        parse_mode=ParseMode.HTML
                    ),
                    description="Try a different search term"
                )
            ]

        await inline_query.answer(
            results=results,
            cache_time=300,
            is_personal=True
        )
    except Exception as e:
        logger.error(f"Error handling inline query: {e}")
        await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    id="error",
                    title="⚠️ Error occurred",
                    input_message_content=InputTextMessageContent(
                        message_text=f"⚠️ Error: {str(e)}",
                        parse_mode=ParseMode.HTML
                    ),
                    description="Try again later"
                )
            ],
            cache_time=0
        )

def create_inline_result(video_info: dict) -> InlineQueryResultArticle:
    """Create an inline result for a specific video info."""
    video_url = f"https://www.youtube.com/watch?v={video_info['id']}"
    result_id = hashlib.md5(video_url.encode()).hexdigest()
    
    title = video_info.get('title', 'Unknown Title')
    uploader = video_info.get('uploader', 'Unknown')
    duration = video_info.get('duration', 0)
    views = video_info.get('view_count', 0)
    upload_date = video_info.get('upload_date', '')
    thumbnail = video_info.get('thumbnail')
    
    # Build description
    desc_parts = []
    if uploader:
        desc_parts.append(f"👤 {uploader}")
    if views:
        desc_parts.append(f"👁️ {format_view_count(views)}")
    if duration:
        desc_parts.append(f"⏱️ {format_duration(duration)}")
    if upload_date:
        desc_parts.append(f"📅 {upload_date}")
    
    description = " | ".join(desc_parts)
    
    content = InputTextMessageContent(
        message_text=f"🎬 <b>{title}</b>\n\n"
                     f"🔗 {video_url}\n\n"
                     f"<i>{description}</i>\n\n"
                     f"Select an action below:",
        parse_mode=ParseMode.HTML
    )
    
    return InlineQueryResultArticle(
        id=result_id,
        title=title[:60] + "..." if len(title) > 60 else title,  # Telegram limits title
        input_message_content=content,
        description=description[:200],  # Truncate if too long
        thumbnail_url=thumbnail,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Download", url=f"https://t.me/Night77_tube_bot?start=dl_{video_info['id']}"),
                InlineKeyboardButton(text="📝 AI Summary", url=f"https://t.me/Night77_tube_bot?start=sum_{video_info['id']}")
            ]
        ])
    )

def create_inline_result_from_search(video: dict) -> InlineQueryResultArticle:
    """Create an inline result from search data."""
    video_url = video.get('url')
    video_id = video.get('id')
    result_id = hashlib.md5(video_url.encode()).hexdigest()
    
    title = video.get('title', 'Unknown Title')
    thumbnail = video.get('thumbnail')
    views = video.get('view_count', 0)
    duration = video.get('duration', 0)
    uploader = video.get('uploader', 'Unknown')
    upload_date = video.get('upload_date', '')
    
    # Build description
    desc_parts = []
    if uploader and uploader != 'Unknown':
        desc_parts.append(f"👤 {uploader}")
    if views:
        desc_parts.append(f"👁️ {format_view_count(views)}")
    if duration:
        desc_parts.append(f"⏱️ {format_duration(duration)}")
    if upload_date:
        desc_parts.append(f"📅 {upload_date}")
    
    description = " | ".join(desc_parts) if desc_parts else "YouTube Video"
    
    content = InputTextMessageContent(
        message_text=f"🎬 <b>{title}</b>\n\n"
                     f"🔗 {video_url}\n\n"
                     f"<i>{description}</i>\n\n"
                     f"Select an action below:",
        parse_mode=ParseMode.HTML
    )
    
    return InlineQueryResultArticle(
        id=result_id,
        title=title[:60] + "..." if len(title) > 60 else title,
        input_message_content=content,
        description=description[:200],
        thumbnail_url=thumbnail,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Download", url=f"https://t.me/Night77_tube_bot?start=dl_{video_id}"),
                InlineKeyboardButton(text="📝 AI Summary", url=f"https://t.me/Night77_tube_bot?start=sum_{video_id}")
            ]
        ])
    )
