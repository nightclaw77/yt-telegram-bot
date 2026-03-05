"""Direct URL downloader service."""
from __future__ import annotations

import aiohttp
from pathlib import Path
from urllib.parse import urlparse

from bot.config import config


class DirectFetchService:
    async def download(self, url: str, prefix: str = "direct") -> Path | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None

        name = Path(parsed.path).name or "file.bin"
        out = config.DOWNLOADS_DIR / f"{prefix}_{name}"

        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, allow_redirects=True) as r:
                if r.status != 200:
                    return None
                with out.open("wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
        return out if out.exists() and out.stat().st_size > 0 else None


direct_fetch_service = DirectFetchService()
