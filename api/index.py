"""Vercel entry point: Telegram webhook -> the same handlers bot.py uses locally.

Telegram gets a 200 immediately; drafting (1-2 min) continues in the background via
wait_until, bounded by the function's maxDuration (vercel.json).
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Header, Request, Response  # noqa: E402
from telegram import Update  # noqa: E402
from vercel.functions import wait_until  # noqa: E402

import store  # noqa: E402
from bot import build_application  # noqa: E402
from config import settings  # noqa: E402

log = logging.getLogger("webhook")
app = FastAPI()
_tg = None


async def telegram_app():
    global _tg
    if _tg is None:
        _tg = build_application(webhook=True)
        await _tg.initialize()
    return _tg


def run_in_background(coro) -> None:
    if os.getenv("VERCEL"):
        wait_until(coro)
    else:  # local uvicorn testing
        asyncio.get_running_loop().create_task(coro)


@app.get("/")
@app.get("/api")
async def health():
    return {"ok": True, "service": "meera-linkedin-bot", "webhook": "/api/telegram"}


@app.post("/api/telegram")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    # Only Telegram knows the secret (set via set_webhook.py), so random POSTs are rejected
    if not settings.webhook_secret or x_telegram_bot_api_secret_token != settings.webhook_secret:
        return Response(status_code=401)
    data = await request.json()

    # Telegram retries on slow/failed responses; never process the same update twice
    update_id = data.get("update_id")
    if update_id is not None:
        if await store.get(f"update:{update_id}"):
            return {"ok": True, "duplicate": True}
        await store.set(f"update:{update_id}", 1, ttl=store.DAY)

    tg = await telegram_app()
    run_in_background(tg.process_update(Update.de_json(data, tg.bot)))
    return {"ok": True}
