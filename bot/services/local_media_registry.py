"""In-memory registry mapping sent Telegram file_ids to local file paths."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

_REG: dict[int, dict[str, str]] = defaultdict(dict)
_ORDER: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=200))


def remember(user_id: int, file_id: str, local_path: str) -> None:
    if not file_id:
        return
    _REG[user_id][file_id] = local_path
    _ORDER[user_id].append(file_id)


def resolve(user_id: int, file_id: str) -> str | None:
    p = _REG.get(user_id, {}).get(file_id)
    if p and Path(p).exists():
        return p
    return None
