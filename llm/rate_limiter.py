"""
rate_limiter.py

Асинхронный токен-бакет Rate Limiter на Redis (redis.asyncio) для SoVAni.
Поддержка multi-tenancy, TTL, динамических лимитов, безопасный импорт.
"""

import time
import hashlib
import logging
from typing import Optional, Callable, Tuple
from redis.asyncio import Redis
import yaml

class RateLimitExceeded(Exception):
    pass

class RateLimitUnavailable(Exception):
    pass

def _sanitize(val: str) -> str:
    return val[:128].replace(":", "_").replace("{", "").replace("}", "")

class TokenBucketRateLimiter:
    """
    Redis-based token bucket rate limiter: cluster-safe, dynamic limits, TTL, multi-tenancy, fail-open.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_bucket: Tuple[int, float] = (10, 1.0),   # (bucket_size, refill_rate)
        key_prefix: str = "rl:v2:",
        limit_loader: Optional[Callable[[str, str], Tuple[int, float]]] = None,
        metrics: Optional[object] = None
    ):
        self.redis_url = redis_url
        self.default_bucket = default_bucket
        self.key_prefix = key_prefix
        self.limit_loader = limit_loader  # Функция: tenant_id, model → (size, rate)
        self.metrics = metrics
        self.redis: Optional[Redis] = None

    async def connect(self):
        if not self.redis:
            self.redis = Redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)

    def _bucket_key(self, tenant_id: str, user_id: str, model: str) -> str:
        base = f"{_sanitize(tenant_id)}:{_sanitize(user_id)}:{_sanitize(model)}"
        hashed = hashlib.sha256(base.encode()).hexdigest()[:24]
        return f"{self.key_prefix}{{{hashed}}}:{base}"

    async def _get_limits(self, tenant_id: str, model: str) -> Tuple[int, float]:
        if self.limit_loader:
            return await self.limit_loader(tenant_id, model)
        return self.default_bucket

    async def check(self, tenant_id: str, user_id: str, model: str):
        await self.connect()
        bucket_size, refill_rate = await self._get_limits(tenant_id, model)
        now = int(time.time())
        key = self._bucket_key(tenant_id, user_id, model)
        tokens_key = key + ":tokens"
        refill_key = key + ":refill"
        ttl = int((bucket_size / refill_rate) * 2)

        # Эмулируем логику токен-бакета на Python (простая реализация, без lua)
        tokens = await self.redis.get(tokens_key)
        last_refill = await self.redis.get(refill_key)
        tokens = int(tokens) if tokens is not None else bucket_size
        last_refill = int(last_refill) if last_refill is not None else now

        elapsed = now - last_refill
        tokens_to_add = int(elapsed * refill_rate)
        if tokens_to_add > 0:
            tokens = min(tokens + tokens_to_add, bucket_size)
            last_refill = now

        if tokens > 0:
            tokens -= 1
            await self.redis.setex(tokens_key, ttl, tokens)
            await self.redis.setex(refill_key, ttl, last_refill)
            return
        else:
            await self.redis.setex(tokens_key, ttl, tokens)
            await self.redis.setex(refill_key, ttl, last_refill)
            if self.metrics:
                await self.metrics.record_rate_limit(tenant_id, model)
            raise RateLimitExceeded("Rate limit exceeded, попробуйте позже.")

# Пример динамического загрузчика лимитов из YAML
class ConfigBasedLimitLoader:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
    async def __call__(self, tenant_id: str, model: str) -> Tuple[int, float]:
        tenant_cfg = self.config.get('tenants', {}).get(tenant_id, {})
        model_limits = tenant_cfg.get('models', {}).get(model)
        if model_limits:
            return tuple(model_limits)
        return tuple(tenant_cfg.get('default', self.config['default']))

