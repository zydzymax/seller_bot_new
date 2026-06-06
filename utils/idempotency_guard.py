"""
Idempotency guards to prevent duplicate bot responses
"""

import time
import hashlib
from typing import Optional
import redis.asyncio as redis
import os

# Redis connection for idempotency
_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_r = redis.from_url(_redis_url)


async def send_once_guard(chat_id: int, update_id: int) -> bool:
    """
    Returns True if we can send (first time),
    False if response to this update was already sent.
    """
    key = f"sent:turn:{chat_id}:{update_id}"
    # NX + TTL 5 minutes
    ok = await _r.set(key, int(time.time()), nx=True, ex=300)
    return bool(ok)


async def is_duplicate_text(chat_id: int, text: str) -> bool:
    """
    Check if this exact text was already sent to this chat recently.
    Returns True if duplicate, False if unique.
    """
    key = f"last_bot_hash:{chat_id}"
    h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    prev = await _r.getset(key, h)  # returns old value, sets new
    await _r.expire(key, 180)  # 3 min TTL
    return prev is not None and prev.decode() == h


def parse_quantity_improved(text: str) -> Optional[int]:
    """
    Enhanced quantity parser that accepts:
    - 4000, 4 000, 4000 шт, около 4000, ~4000
    """
    import re

    # Remove common prefixes and normalize spaces
    clean = re.sub(r"^\s*(около|примерно|~|≈)\s*", "", text.lower())
    clean = re.sub(r"\s+", " ", clean.strip())

    # Extract number, allowing spaces within digits
    m = re.search(r"\b(\d[\d\s]*\d|\d)\b", clean)
    if not m:
        return None

    # Remove spaces and convert
    raw_num = re.sub(r"\s+", "", m.group(1))
    try:
        return int(raw_num)
    except ValueError:
        return None
