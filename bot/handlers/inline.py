"""Inline query handler for searching videos and channels."""
import logging
import hashlib
from typing import List

from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from bot.services.youtube import YouTubeService

logger = logging.getLogger(__name__)
router = Router()
youtube_service = YouTubeService()

@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """Handle inline query for YouTube search."""
    query = inline_query.query.strip()
    if not query:
        return

    logger.info(f"Inline search query: {query}")
    
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

    await inline_query.answer(
        results=results,
        cache_time=300,
        is_personal=True
    )

def create_inline_result(video_info: dict) -> InlineQueryResultArticle:
    """Create an inline result for a specific video info."""
    video_url = f"https://www.youtube.com/watch?v={video_info['id']}"
    result_id = hashlib.md5(video_url.encode()).hexdigest()
    
    title = video_info.get('title', 'Unknown Title')
    uploader = video_info.get('uploader', 'Unknown')
    duration = video_info.get('duration', 0)
    minutes = duration // 60
    seconds = duration % 60
    
    description = f"👤 {uploader} | ⏱️ {minutes}:{seconds:02d}"
    
    content = InputTextMessageContent(
        message_text=f"🎬 <b>{title}</b>\n\n🔗 {video_url}\n\n<i>Select an action below:</i>",
        parse_mode=ParseMode.HTML
    )
    
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        input_message_content=content,
        description=description,
        thumbnail_url=video_info.get('thumbnail'),
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
    
    content = InputTextMessageContent(
        message_text=f"🎬 <b>{title}</b>\n\n🔗 {video_url}\n\n<i>Select an action below:</i>",
        parse_mode=ParseMode.HTML
    )
    
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        input_message_content=content,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Download", url=f"https://t.me/Night77_tube_bot?start=dl_{video_id}"),
                InlineKeyboardButton(text="📝 AI Summary", url=f"https://t.me/Night77_tube_bot?start=sum_{video_id}")
            ]
        ])
    )
