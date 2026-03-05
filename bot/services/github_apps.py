"""GitHub app search and release asset download helpers."""
from __future__ import annotations

import os
import aiohttp
from pathlib import Path
from typing import List, Dict, Optional

from bot.config import config

UA = "Night77-Tube-Bot/1.0"


def human_size(n: int) -> str:
    size = float(n or 0)
    units = ["B", "KB", "MB", "GB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f}{units[i]}"


class GitHubAppsService:
    async def search_repos(self, query: str, limit: int = 6) -> List[Dict]:
        url = "https://api.github.com/search/repositories"
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max(limit, 1), 10),
        }
        headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, headers=headers) as r:
                if r.status != 200:
                    return []
                data = await r.json()
                out = []
                for it in data.get("items", []):
                    out.append({
                        "full_name": it.get("full_name"),
                        "stars": it.get("stargazers_count", 0),
                        "owner": (it.get("owner") or {}).get("login", ""),
                        "description": it.get("description") or "",
                        "html_url": it.get("html_url") or "",
                    })
                return out

    async def latest_release_assets(self, full_name: str) -> Dict:
        url = f"https://api.github.com/repos/{full_name}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    return {"tag": "", "assets": []}
                data = await r.json()
                assets = []
                for a in data.get("assets", []):
                    assets.append({
                        "name": a.get("name", ""),
                        "size": a.get("size", 0),
                        "size_h": human_size(a.get("size", 0)),
                        "url": a.get("browser_download_url", ""),
                    })
                return {"tag": data.get("tag_name", ""), "assets": assets}

    def pick_target_assets(self, assets: List[Dict]) -> Dict[str, Optional[Dict]]:
        android = None
        windows = None
        for a in assets:
            n = (a.get("name") or "").lower()
            if not android and ("arm64" in n or "aarch64" in n or "v8a" in n) and n.endswith((".apk", ".xapk", ".apkm")):
                android = a
            if not windows and ("win" in n or "windows" in n) and ("x64" in n or "amd64" in n or "64" in n) and n.endswith((".exe", ".msi", ".zip")):
                windows = a
        return {"android_v8a": android, "windows_x64": windows}

    async def download_asset(self, url: str, out_name: str) -> Optional[Path]:
        out = config.DOWNLOADS_DIR / f"gh_{out_name}"
        out.parent.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": UA}

        mirrors = [m.strip() for m in os.getenv("GITHUB_DOWNLOAD_MIRRORS", "").split(",") if m.strip()]
        candidates = [url] + [m + url for m in mirrors]

        async with aiohttp.ClientSession() as s:
            for candidate in candidates:
                for _ in range(3):
                    try:
                        async with s.get(candidate, headers=headers) as r:
                            if r.status != 200:
                                continue
                            with out.open("wb") as f:
                                async for chunk in r.content.iter_chunked(256 * 1024):
                                    f.write(chunk)
                            if out.exists() and out.stat().st_size > 0:
                                return out
                    except Exception:
                        continue
        return None


github_apps_service = GitHubAppsService()
