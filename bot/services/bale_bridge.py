"""Bale messenger bridge service for forwarding downloaded files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
import asyncio

import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)


class BaleBridgeService:
    """Forward local files to Bale Bot API using multipart/form-data uploads."""

    def __init__(self) -> None:
        self.token = config.BALE_BOT_TOKEN
        self.chat_id = config.BALE_CHAT_ID
        self.relay_url = (config.BALE_RELAY_URL or "").rstrip("/")
        self.relay_token = config.BALE_RELAY_TOKEN or ""
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
            "photo": ("sendPhoto", "photo"),
        }
        method, media_field = method_map.get(media_type, ("sendDocument", "document"))

        endpoint = f"https://tapi.bale.ai/bot{self.token}/{method}"

        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=300)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    with path.open("rb") as f:
                        form = aiohttp.FormData()
                        form.add_field("chat_id", str(self.chat_id))
                        if caption:
                            form.add_field("caption", caption[:900])
                        form.add_field(
                            media_field,
                            f,
                            filename=path.name,
                            content_type="application/octet-stream",
                        )
                        async with session.post(endpoint, data=form) as resp:
                            body = await resp.text()
                            try:
                                data = await resp.json(content_type=None)
                            except Exception:
                                data = {"ok": False, "error_code": resp.status, "description": body[:300]}

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
                        with path.open("rb") as f2:
                            fallback_form.add_field(
                                "document",
                                f2,
                                filename=path.name,
                                content_type="application/octet-stream",
                            )
                            async with session.post(fallback_endpoint, data=fallback_form) as f_resp:
                                f_body = await f_resp.text()
                                try:
                                    f_data = await f_resp.json(content_type=None)
                                except Exception:
                                    f_data = {"ok": False, "error_code": f_resp.status, "description": f_body[:300]}
                                f_ok = bool(f_data.get("ok"))
                                if f_ok:
                                    return True
                                logger.error("Bale fallback sendDocument error: %s", f_data)

                    # retry only for transient 5xx/connectivity cases
                    if int(data.get("error_code", 0)) >= 500 and attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    return False
            except Exception:
                logger.exception("Bale forward failed (attempt %s)", attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                return False

        # final fallback via relay node (if configured)
        return await self._relay_file(path, media_type, caption)


    async def _relay_file(self, path: Path, media_type: str, caption: Optional[str]) -> bool:
        if not self.relay_url:
            return False
        endpoint = f"{self.relay_url}/relay/file"
        headers = {"x-relay-token": self.relay_token} if self.relay_token else {}
        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=300)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    form = aiohttp.FormData()
                    form.add_field("media_type", media_type)
                    if caption:
                        form.add_field("caption", caption[:900])
                    with path.open("rb") as f:
                        form.add_field("file", f, filename=path.name, content_type="application/octet-stream")
                        async with session.post(endpoint, data=form, headers=headers) as resp:
                            data = await resp.json(content_type=None)
                            if bool(data.get("ok")):
                                return True
                await asyncio.sleep(1.2 * (attempt + 1))
            except Exception:
                logger.exception("Bale relay file forward failed (attempt %s)", attempt + 1)
                await asyncio.sleep(1.2 * (attempt + 1))
        return False

    async def _relay_text(self, text: str) -> bool:
        if not self.relay_url:
            return False
        endpoint = f"{self.relay_url}/relay/text"
        headers = {"x-relay-token": self.relay_token} if self.relay_token else {}
        payload = {"text": text[:3800]}
        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(endpoint, json=payload, headers=headers) as resp:
                        data = await resp.json(content_type=None)
                        if bool(data.get("ok")):
                            return True
                await asyncio.sleep(1.2 * (attempt + 1))
            except Exception:
                logger.exception("Bale relay text forward failed (attempt %s)", attempt + 1)
                await asyncio.sleep(1.2 * (attempt + 1))
        return False


    async def forward_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        endpoint = f"https://tapi.bale.ai/bot{self.token}/sendMessage"
        payload = {"chat_id": str(self.chat_id), "text": text[:3800]}
        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(endpoint, json=payload) as resp:
                        data = await resp.json(content_type=None)
                        ok = bool(data.get("ok"))
                        if ok:
                            return True
                        logger.error("Bale sendMessage error: %s", data)
                        if int(data.get("error_code", 0)) >= 500 and attempt < 2:
                            await asyncio.sleep(1.2 * (attempt + 1))
                            continue
                        break
            except Exception:
                logger.exception("Bale text forward failed (attempt %s)", attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                break
        return await self._relay_text(text)


bale_bridge_service = BaleBridgeService()
