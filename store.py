"""Small JSON key-value store for bot state (drafts, pending edits, note buffers).

On Vercel this is the Runtime Cache, so state survives between serverless invocations.
Locally the same API falls back to an in-memory cache automatically.
"""

import json
import logging

from vercel.functions import AsyncRuntimeCache

log = logging.getLogger(__name__)
_cache = AsyncRuntimeCache(namespace="meera-bot")

DAY = 86400


async def get(key: str):
    try:
        raw = await _cache.get(key)
    except Exception as e:  # cache outages must not crash the bot
        log.warning("store.get(%s) failed: %s", key, e)
        return None
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


async def set(key: str, value, ttl: int = 14 * DAY) -> None:
    try:
        await _cache.set(key, json.dumps(value, ensure_ascii=False), {"ttl": ttl})
    except Exception as e:
        log.warning("store.set(%s) failed: %s", key, e)


async def delete(key: str) -> None:
    try:
        await _cache.delete(key)
    except Exception as e:
        log.warning("store.delete(%s) failed: %s", key, e)
