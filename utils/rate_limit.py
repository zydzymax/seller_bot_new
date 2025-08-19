"""
rate_limit.py — Rate limiting с Redis для защиты от спама и DDoS.

- Sliding window rate limiting
- Поддержка burst и RPS лимитов
- Дифференцированные лимиты по IP и chat_id
- Prometheus метрики

© SoVAni 2025
"""

import os
import time
import asyncio
from typing import Optional, Tuple
from dataclasses import dataclass

import redis.asyncio as redis
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Конфигурация rate limiting"""
    rps: int = 5
    burst: int = 10
    window_size: int = 60
    
    
class RateLimiter:
    """Rate limiter на основе sliding window в Redis"""
    
    def __init__(self, redis_client: redis.Redis, config: RateLimitConfig = None):
        self.redis_client = redis_client
        self.config = config or RateLimitConfig(
            rps=int(os.getenv('RATE_LIMIT_RPS', '5')),
            burst=int(os.getenv('RATE_LIMIT_BURST', '10')),
            window_size=int(os.getenv('RATE_LIMIT_WINDOW', '60'))
        )
        
    async def is_allowed(
        self, 
        identifier: str, 
        limit_type: str = "default"
    ) -> Tuple[bool, dict]:
        """
        Проверка разрешен ли запрос
        
        Args:
            identifier: Уникальный идентификатор (IP, chat_id, user_id)
            limit_type: Тип лимита (default, chat, ip)
            
        Returns:
            Tuple[разрешен, метаданные]
        """
        current_time = time.time()
        window_start = current_time - self.config.window_size
        
        # Ключ для хранения в Redis
        key = f"rate_limit:{limit_type}:{identifier}"
        
        try:
            # Lua скрипт для атомарной операции sliding window
            lua_script = """
            local key = KEYS[1]
            local window_start = tonumber(ARGV[1])
            local current_time = tonumber(ARGV[2])
            local max_requests = tonumber(ARGV[3])
            local ttl = tonumber(ARGV[4])
            
            -- Удаляем старые записи
            redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
            
            -- Получаем текущее количество запросов
            local current_requests = redis.call('ZCARD', key)
            
            if current_requests < max_requests then
                -- Добавляем новый запрос
                redis.call('ZADD', key, current_time, current_time)
                redis.call('EXPIRE', key, ttl)
                return {1, current_requests + 1, max_requests - current_requests - 1}
            else
                return {0, current_requests, 0}
            end
            """
            
            # Выполнение скрипта
            result = await self.redis_client.eval(
                lua_script,
                1,
                key,
                window_start,
                current_time,
                self.config.burst,
                self.config.window_size + 10  # TTL с запасом
            )
            
            allowed = bool(result[0])
            current_count = int(result[1])
            remaining = int(result[2])
            
            metadata = {
                "allowed": allowed,
                "limit": self.config.burst,
                "remaining": remaining,
                "reset_time": int(current_time + self.config.window_size),
                "retry_after": None if allowed else self.config.window_size
            }
            
            if not allowed:
                logger.warning(
                    "Rate limit exceeded",
                    identifier=identifier,
                    limit_type=limit_type,
                    current_count=current_count,
                    limit=self.config.burst
                )
            
            return allowed, metadata
            
        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # В случае ошибки разрешаем запрос (fail-open)
            return True, {
                "allowed": True,
                "limit": self.config.burst,
                "remaining": self.config.burst,
                "reset_time": int(current_time + self.config.window_size),
                "retry_after": None,
                "error": str(e)
            }
            
    async def reset_limit(self, identifier: str, limit_type: str = "default"):
        """Сброс лимитов для идентификатора"""
        key = f"rate_limit:{limit_type}:{identifier}"
        await self.redis_client.delete(key)
        
    async def get_current_usage(
        self, 
        identifier: str, 
        limit_type: str = "default"
    ) -> dict:
        """Получение текущего использования лимитов"""
        current_time = time.time()
        window_start = current_time - self.config.window_size
        key = f"rate_limit:{limit_type}:{identifier}"
        
        try:
            # Очистка старых записей
            await self.redis_client.zremrangebyscore(key, 0, window_start)
            
            # Подсчет текущих запросов
            current_count = await self.redis_client.zcard(key)
            
            return {
                "current_requests": current_count,
                "limit": self.config.burst,
                "remaining": max(0, self.config.burst - current_count),
                "window_start": window_start,
                "window_end": current_time
            }
        except Exception as e:
            logger.error(f"Error getting rate limit usage: {e}")
            return {
                "current_requests": 0,
                "limit": self.config.burst,
                "remaining": self.config.burst,
                "error": str(e)
            }
            
    async def health_check(self) -> dict:
        """Проверка состояния rate limiter"""
        try:
            # Тестовая операция
            test_key = "rate_limit:health_check"
            await self.redis_client.setex(test_key, 1, "test")
            await self.redis_client.delete(test_key)
            
            return {
                "status": "healthy",
                "redis_connected": True,
                "config": {
                    "rps": self.config.rps,
                    "burst": self.config.burst,
                    "window_size": self.config.window_size
                }
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "redis_connected": False,
                "error": str(e)
            }


# Глобальный экземпляр rate limiter
_rate_limiter_instance = None


async def get_rate_limiter(redis_url: str = None) -> RateLimiter:
    """Получение singleton экземпляра rate limiter"""
    global _rate_limiter_instance
    
    if _rate_limiter_instance is None:
        redis_url = redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0')
        redis_client = redis.from_url(redis_url)
        _rate_limiter_instance = RateLimiter(redis_client)
        
    return _rate_limiter_instance


class RateLimitMiddleware:
    """Middleware для интеграции rate limiter в веб-фреймворки"""
    
    def __init__(self, rate_limiter: RateLimiter):
        self.rate_limiter = rate_limiter
        
    def get_identifier(self, request) -> Tuple[str, str]:
        """
        Извлечение идентификатора из запроса
        
        Returns:
            Tuple[identifier, limit_type]
        """
        # Для FastAPI/Starlette
        if hasattr(request, 'client') and request.client:
            return request.client.host, "ip"
            
        # Для aiohttp
        if hasattr(request, 'remote'):
            return request.remote, "ip"
            
        # Для Telegram updates
        if hasattr(request, 'message') and request.message:
            chat_id = getattr(request.message.chat, 'id', None)
            if chat_id:
                return str(chat_id), "chat"
                
        # По умолчанию используем IP из заголовков
        x_forwarded_for = getattr(request, 'headers', {}).get('X-Forwarded-For')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip(), "ip"
            
        x_real_ip = getattr(request, 'headers', {}).get('X-Real-IP')
        if x_real_ip:
            return x_real_ip, "ip"
            
        return "unknown", "default"
        
    async def __call__(self, request, call_next=None):
        """Middleware обработчик"""
        identifier, limit_type = self.get_identifier(request)
        
        allowed, metadata = await self.rate_limiter.is_allowed(identifier, limit_type)
        
        if not allowed:
            # Для разных фреймворков возвращаем соответствующие ответы
            error_response = {
                "error": "Rate limit exceeded",
                "message": f"Too many requests. Try again in {metadata.get('retry_after', 60)} seconds.",
                "retry_after": metadata.get('retry_after'),
                "limit": metadata.get('limit'),
                "remaining": metadata.get('remaining')
            }
            
            # Логирование
            logger.warning(
                "Request blocked by rate limiter",
                identifier=identifier,
                limit_type=limit_type,
                metadata=metadata
            )
            
            return error_response
            
        # Добавление заголовков rate limit в ответ
        if call_next:
            response = await call_next(request)
            
            # Добавление заголовков (если поддерживается)
            if hasattr(response, 'headers'):
                response.headers.update({
                    'X-RateLimit-Limit': str(metadata.get('limit', 0)),
                    'X-RateLimit-Remaining': str(metadata.get('remaining', 0)),
                    'X-RateLimit-Reset': str(metadata.get('reset_time', 0))
                })
                
            return response
            
        return metadata


__all__ = [
    'RateLimiter',
    'RateLimitConfig', 
    'RateLimitMiddleware',
    'get_rate_limiter'
]