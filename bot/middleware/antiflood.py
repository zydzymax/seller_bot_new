"""
antiflood.py — production-ready антифлуд middleware для SoVAni AI-продавца.

- Redis-реализация: скользящее окно, атомарный Lua-скрипт
- In-memory TTLCache: автоочистка и ограничение памяти
- Fallback с обработкой ошибок Redis
- Хеширование user_id в логах
- DI и прометей-метрика (placeholder)
"""

import asyncio
import hashlib
import time
from typing import Optional

try:
    from cachetools import TTLCache
except ImportError:
    TTLCache = dict  # fallback, но рекомендую установить cachetools

# Prometheus placeholder (реальную логику добавить при интеграции)
def prometheus_flood_event(user_id_hash):
    pass

class AsyncRedisFloodControl:
    """
    Redis-реализация скользящего окна (Lua-скрипт, атомарность)
    """
    LUA_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
    redis.call('ZADD', key, now, tostring(now))
    redis.call('EXPIRE', key, window)
    local count = redis.call('ZCARD', key)
    if count > limit then
        return 1
    end
    return 0
    """

    def __init__(self, redis, rate_limit: int = 3, interval_sec: int = 10, fallback=None):
        self.redis = redis
        self.rate_limit = rate_limit
        self.interval_sec = interval_sec
        self.fallback = fallback or InMemoryFloodControl(rate_limit, interval_sec)

    async def is_flooding(self, user_id: int) -> bool:
        key = f"antiflood:{user_id}"
        now = time.time()
        user_id_hash = hashlib.sha256(str(user_id).encode()).hexdigest()
        try:
            result = await self.redis.eval(
                self.LUA_SCRIPT, 1, key, now, self.interval_sec, self.rate_limit
            )
            if result == 1:
                prometheus_flood_event(user_id_hash)
            return bool(result)
        except Exception as e:
            # Fallback на in-memory
            print(f"[WARN] Redis error in antiflood: {e}")
            return await self.fallback.is_flooding(user_id)

class InMemoryFloodControl:
    """
    Асинхронный in-memory TTLCache с автоочисткой.
    """
    def __init__(self, rate_limit: int = 3, interval_sec: int = 10, max_size=10000):
        self.rate_limit = rate_limit
        self.interval_sec = interval_sec
        self._cache = TTLCache(maxsize=max_size, ttl=interval_sec * 2)
        self._lock = asyncio.Lock()

    async def is_flooding(self, user_id: int) -> bool:
        now = time.monotonic()
        user_id_hash = hashlib.sha256(str(user_id).encode()).hexdigest()
        async with self._lock:
            timestamps = self._cache.get(user_id, [])
            # Оставляем только актуальные записи
            timestamps = [t for t in timestamps if now - t < self.interval_sec]
            timestamps.append(now)
            self._cache[user_id] = timestamps
            if len(timestamps) > self.rate_limit:
                prometheus_flood_event(user_id_hash)
                return True
            # Автоочистка устаревших user_id (TTLCache делает это автоматически)
            return False

class AntiFloodMiddleware:
    """
    DI-ready антифлуд middleware: Redis или in-memory, безопасное логирование
    """
    def __init__(self, redis=None, rate_limit=3, interval_sec=10):
        if redis:
            self.backend = AsyncRedisFloodControl(redis, rate_limit, interval_sec)
        else:
            self.backend = InMemoryFloodControl(rate_limit, interval_sec)

    async def is_flooding(self, user_id: int) -> bool:
        return await self.backend.is_flooding(user_id)

# ---- Для теста ----
if __name__ == "__main__":
    async def test():
        flood = InMemoryFloodControl(rate_limit=2, interval_sec=3)
        uid = 12345
        for i in range(5):
            print(f"Test {i}: flooding = {await flood.is_flooding(uid)}")
            await asyncio.sleep(1)
    asyncio.run(test())

