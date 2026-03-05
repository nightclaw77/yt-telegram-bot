"""Create Bale-only password-protected archive packages."""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Optional

from bot.config import config

DEFAULT_PASSWORD = "924780166Vf"


async def create_secure_zip(input_path: Path, compression_level: str = "medium", password: str = DEFAULT_PASSWORD) -> Optional[Path]:
    """Create password-protected zip using system zip command."""
    if shutil.which("zip") is None:
        return None

    level_map = {
        "low": "-1",
        "medium": "-5",
        "high": "-9",
    }
    level_flag = level_map.get(compression_level, "-5")

    out_dir = config.DOWNLOADS_DIR / "bale_secure"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"secure_{uuid.uuid4().hex[:10]}.zip"

    cmd = [
        "zip",
        "-j",
        level_flag,
        "-P",
        password,
        str(out_path),
        str(input_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, _ = await proc.communicate()
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        return None
    return out_path
