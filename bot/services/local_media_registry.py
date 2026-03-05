"""Persistent registry mapping Telegram file_ids to local file paths."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.getenv("DATABASE_FILE", "bot.db"))
TTL_SECONDS = 6 * 3600


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS local_media_registry (
          user_id INTEGER NOT NULL,
          file_id TEXT NOT NULL,
          local_path TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          PRIMARY KEY(user_id, file_id)
        )
        """
    )
    c.commit()
    return c


def remember(user_id: int, file_id: str, local_path: str) -> None:
    if not file_id:
        return
    p = Path(local_path)
    if not p.exists():
        return
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO local_media_registry(user_id,file_id,local_path,created_at) VALUES(?,?,?,?)",
            (user_id, file_id, str(p), now),
        )
        c.execute("DELETE FROM local_media_registry WHERE created_at < ?", (now - TTL_SECONDS,))
        c.commit()


def resolve(user_id: int, file_id: str) -> str | None:
    now = int(time.time())
    with _conn() as c:
        c.execute("DELETE FROM local_media_registry WHERE created_at < ?", (now - TTL_SECONDS,))
        row = c.execute(
            "SELECT local_path FROM local_media_registry WHERE user_id=? AND file_id=?",
            (user_id, file_id),
        ).fetchone()
        if not row:
            return None
        p = Path(row[0])
        return str(p) if p.exists() else None
