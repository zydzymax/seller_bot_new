import asyncio
import hashlib
import time

try:
    from cachetools import TTLCache
except ImportError:
    TTLCache = dict

class AsyncRedisFloodControl:
    LUA_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
    redis.call('ZADD', key, now, tostring(now))
    redis.call('EXPIRE', key, window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        return 1
    end
    return 0
    """

    def __init__(self, redis, rate_limit=3, interval_sec=10, fallback=None):
        self.redis = redis
        self.rate_limit = rate_limit
        self.interval_sec = interval_sec
        self.fallback = fallback or InMemoryFloodControl(rate_limit, interval_sec)

    def _user_hash(self, user_id: int) -> str:
        salt = b"sovani_anti_flood_salt"
        return hashlib.blake2b(str(user_id).encode(), key=salt, digest_size=16).hexdigest()

    async def is_limited(self, user_id: int) -> bool:
        user_hash = self._user_hash(user_id)
        key = f"antiflood:{user_hash}"
        try:
            result = await self.redis.eval(
                self.LUA_SCRIPT, 1, key, time.time(), self.interval_sec, self.rate_limit
            )
            return bool(result)
        except Exception as e:
            print(f"[ANTIFLOOD] Redis error: {e} — fallback in-memory")
            return await self.fallback.is_limited(user_id)

class InMemoryFloodControl:
    def __init__(self, rate_limit=3, interval_sec=10, max_size=10000):
        self.rate_limit = rate_limit
        self.interval_sec = interval_sec
        self._cache = TTLCache(maxsize=max_size, ttl=interval_sec * 2)
        self._lock = asyncio.Lock()

    def _user_hash(self, user_id: int) -> str:
        return hashlib.sha3_256(str(user_id).encode()).hexdigest()[:16]

    async def is_limited(self, user_id: int) -> bool:
        user_hash = self._user_hash(user_id)
        now = time.monotonic()
        async with self._lock:
            timestamps = self._cache.get(user_id, [])
            timestamps = [t for t in timestamps if now - t < self.interval_sec]
            timestamps.append(now)
            self._cache[user_id] = timestamps
            if len(timestamps) > self.rate_limit:
                print(f"[ANTIFLOOD] Limit! user_hash={user_hash}")
                return True
            return False

class AntiFloodMiddleware:
    def __init__(self, redis=None, rate_limit=3, interval_sec=10):
        if redis:
            self.backend = AsyncRedisFloodControl(redis, rate_limit, interval_sec)
        else:
            self.backend = InMemoryFloodControl(rate_limit, interval_sec)

    async def is_limited(self, user_id: int) -> bool:
        return await self.backend.is_limited(user_id)
