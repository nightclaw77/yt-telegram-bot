"""Run this on an Iran-accessible node to relay requests to Bale API."""
from __future__ import annotations

import os
from pathlib import Path

import aiohttp
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "")
BALE_CHAT_ID = os.getenv("BALE_CHAT_ID", "")
RELAY_TOKEN = os.getenv("BALE_RELAY_TOKEN", "")
TMP_DIR = Path(os.getenv("BALE_RELAY_TMP", "./relay_tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()


def _check_token(x_relay_token: str):
    if RELAY_TOKEN and x_relay_token != RELAY_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


async def _send_to_bale(local_path: Path, media_type: str, caption: str = "") -> bool:
    method_map = {
        "video": ("sendVideo", "video"),
        "audio": ("sendAudio", "audio"),
        "photo": ("sendPhoto", "photo"),
        "document": ("sendDocument", "document"),
    }
    method, field = method_map.get(media_type, ("sendDocument", "document"))
    url = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}/{method}"
    form = aiohttp.FormData()
    form.add_field("chat_id", BALE_CHAT_ID)
    if caption:
        form.add_field("caption", caption[:900])
    with local_path.open("rb") as f:
        form.add_field(field, f, filename=local_path.name, content_type="application/octet-stream")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
            async with s.post(url, data=form) as r:
                data = await r.json(content_type=None)
                return bool(data.get("ok"))


@app.post("/relay/file")
async def relay_file(
    file: UploadFile = File(...),
    media_type: str = Form("document"),
    caption: str = Form(""),
    x_relay_token: str = Header(default=""),
):
    _check_token(x_relay_token)
    if not BALE_BOT_TOKEN or not BALE_CHAT_ID:
        raise HTTPException(status_code=500, detail="bale credentials missing")

    local = TMP_DIR / file.filename
    with local.open("wb") as f:
        f.write(await file.read())
    try:
        ok = await _send_to_bale(local, media_type, caption)
        return {"ok": ok}
    finally:
        local.unlink(missing_ok=True)


@app.post("/relay/text")
async def relay_text(payload: dict, x_relay_token: str = Header(default="")):
    _check_token(x_relay_token)
    text = (payload or {}).get("text", "")
    if not BALE_BOT_TOKEN or not BALE_CHAT_ID:
        raise HTTPException(status_code=500, detail="bale credentials missing")

    url = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}/sendMessage"
    body = {"chat_id": BALE_CHAT_ID, "text": text[:3800]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        async with s.post(url, json=body) as r:
            data = await r.json(content_type=None)
            return {"ok": bool(data.get("ok"))}
