"""TTL-based file cache for reusing recent media outputs."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Optional

from bot.config import config


class FileCacheService:
    def __init__(self) -> None:
        self.enabled = config.FILE_CACHE_ENABLED
        self.ttl = max(300, int(config.FILE_CACHE_TTL_SECONDS))
        self.dir = config.DOWNLOADS_DIR / "cache"
        self.index_path = self.dir / "index.json"
        self.dir.mkdir(parents=True, exist_ok=True)

    def make_key(self, *parts: str) -> str:
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_index(self, data: dict) -> None:
        self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def cleanup_expired(self) -> None:
        if not self.enabled:
            return
        now = int(time.time())
        idx = self._load_index()
        changed = False
        for key, item in list(idx.items()):
            path = Path(item.get("path", ""))
            created_at = int(item.get("created_at", 0))
            expired = now - created_at > self.ttl
            if expired or not path.exists():
                if path.exists():
                    path.unlink(missing_ok=True)
                idx.pop(key, None)
                changed = True
        if changed:
            self._save_index(idx)

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            return None
        self.cleanup_expired()
        idx = self._load_index()
        item = idx.get(key)
        if not item:
            return None
        path = Path(item.get("path", ""))
        if not path.exists():
            idx.pop(key, None)
            self._save_index(idx)
            return None
        return str(path)

    def put(self, key: str, source_path: str | Path) -> Optional[str]:
        if not self.enabled:
            return str(source_path)
        self.cleanup_expired()
        src = Path(source_path)
        if not src.exists():
            return None

        ext = src.suffix or ".bin"
        dest = self.dir / f"cache_{key}{ext}"
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

        idx = self._load_index()
        idx[key] = {
            "path": str(dest),
            "created_at": int(time.time()),
        }
        self._save_index(idx)
        return str(dest)

    def is_cache_file(self, path: str | Path) -> bool:
        try:
            return Path(path).resolve().is_relative_to(self.dir.resolve())
        except Exception:
            return False


file_cache_service = FileCacheService()
