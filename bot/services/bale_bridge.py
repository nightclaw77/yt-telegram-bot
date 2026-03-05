"""Bale messenger bridge service for forwarding downloaded files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)


class BaleBridgeService:
    """Forward local files to Bale Bot API using multipart/form-data uploads."""

    def __init__(self) -> None:
        self.token = config.BALE_BOT_TOKEN
        self.chat_id = config.BALE_CHAT_ID
        self.enabled = bool(config.BALE_FORWARD_ENABLED and self.token and self.chat_id)

    async def forward_file(self, file_path: str | Path, media_type: str, caption: Optional[str] = None) -> bool:
        """Send file to Bale chat. media_type: video | audio | document"""
        if not self.enabled:
            return False

        path = Path(file_path)
        if not path.exists():
            logger.warning("Bale forward skipped, file not found: %s", path)
            return False

        method_map = {
            "video": ("sendVideo", "video"),
            "audio": ("sendAudio", "audio"),
            "document": ("sendDocument", "document"),
        }
        method, media_field = method_map.get(media_type, ("sendDocument", "document"))

        endpoint = f"https://tapi.bale.ai/bot{self.token}/{method}"

        form = aiohttp.FormData()
        form.add_field("chat_id", str(self.chat_id))
        if caption:
            form.add_field("caption", caption[:900])

        with path.open("rb") as f:
            form.add_field(
                media_field,
                f,
                filename=path.name,
                content_type="application/octet-stream",
            )

            try:
                timeout = aiohttp.ClientTimeout(total=300)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(endpoint, data=form) as resp:
                        data = await resp.json(content_type=None)
                        ok = bool(data.get("ok"))
                        if ok:
                            return True

                        logger.error("Bale API error on %s: %s", method, data)

                        # Fallback path: sometimes media-specific endpoints reject while sendDocument accepts
                        if method != "sendDocument":
                            fallback_endpoint = f"https://tapi.bale.ai/bot{self.token}/sendDocument"
                            fallback_form = aiohttp.FormData()
                            fallback_form.add_field("chat_id", str(self.chat_id))
                            if caption:
                                fallback_form.add_field("caption", f"[fallback-doc] {caption[:860]}")
                            f.seek(0)
                            fallback_form.add_field(
                                "document",
                                f,
                                filename=path.name,
                                content_type="application/octet-stream",
                            )
                            async with session.post(fallback_endpoint, data=fallback_form) as f_resp:
                                f_data = await f_resp.json(content_type=None)
                                f_ok = bool(f_data.get("ok"))
                                if not f_ok:
                                    logger.error("Bale fallback sendDocument error: %s", f_data)
                                return f_ok

                        return False
            except Exception:
                logger.exception("Bale forward failed")
                return False


bale_bridge_service = BaleBridgeService()
