import asyncio
import hashlib
import json
import logging
from typing import Optional, Any, Callable
from redis.asyncio import Redis, ConnectionPool

logger = logging.getLogger("llm.cache")
logger.setLevel(logging.INFO)

class SecureCacheManager:
    """
    Асинхронный кэш на Redis с пулом соединений, безопасными ключами, TTL и поддержкой инвалидации.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        prefix: str = "llm:v1:",
        default_ttl: int = 3600,
        max_connections: int = 10,
        serializer: Callable = json.dumps,
        deserializer: Callable = json.loads
    ):
        self.pool = ConnectionPool.from_url(
            redis_url,
            max_connections=max_connections,
            decode_responses=True
        )
        self.prefix = prefix
        self.default_ttl = default_ttl
        self.serializer = serializer
        self.deserializer = deserializer

    def _make_key(self, *parts) -> str:
        raw = ":".join(map(str, parts))
        return self.prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    async def get_redis(self) -> Redis:
        return Redis(connection_pool=self.pool)

    async def get(self, key: str) -> Optional[Any]:
        try:
            redis = await self.get_redis()
            data = await redis.get(key)
            if data is None:
                return None
            try:
                return self.deserializer(data)
            except Exception as e:
                logger.warning(f"Ошибка декодирования кэша: {e}")
                return None
        except Exception as e:
            logger.error(f"Ошибка доступа к Redis: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        try:
            redis = await self.get_redis()
            ttl = ttl or self.default_ttl
            data = self.serializer(value)
            await redis.setex(key, ttl, data)
        except Exception as e:
            logger.error(f"Ошибка сохранения в кэш: {e}")

    async def get_or_set(self, *parts, value_fn=None, ttl=None):
        key = self._make_key(*parts)
        cached = await self.get(key)
        if cached is not None:
            return cached
        if value_fn is not None:
            value = await value_fn()
            await self.set(key, value, ttl)
            return value
        return None

    async def invalidate(self, key: str):
        try:
            redis = await self.get_redis()
            await redis.delete(key)
        except Exception as e:
            logger.error(f"Ошибка инвалидации кэша: {e}")

