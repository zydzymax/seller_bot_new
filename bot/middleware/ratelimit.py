"""
Rate limiting middleware для SoVAni AI Seller webhook
с graceful fallback и robust error handling
"""

import asyncio
import json
import time
import logging
from typing import Dict, Any, Optional, Set
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Попытка импорта Redis с fallback
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis not available, using in-memory rate limiting")

# Попытка импорта Prometheus metrics с fallback    
try:
    from prometheus_client import Counter
    WEBHOOK_LIMITED = Counter("webhook_limited_total", "Webhook limits", ["type"])
    WEBHOOK_DUPLICATES = Counter("webhook_duplicates_total", "Webhook duplicates")
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not available, skipping metrics")


@dataclass
class RateLimitConfig:
    """Конфигурация rate limiting"""
    ip_rps: int = 10
    ip_burst: int = 20
    chat_rps: int = 3
    chat_burst: int = 6
    body_limit: int = 2_097_152  # 2MB
    dup_ttl: int = 60  # seconds


class InMemoryRateLimit:
    """In-memory реализация rate limiting для fallback"""
    
    def __init__(self):
        self.lock = asyncio.Lock()
        self.ip_buckets: Dict[str, deque] = defaultdict(deque)
        self.chat_buckets: Dict[str, deque] = defaultdict(deque)
        self.duplicates: Dict[str, float] = {}
        
    async def check_rate_limit(self, key: str, limit: int, window: int = 1) -> tuple[bool, dict]:
        """Проверка rate limit с token bucket algorithm"""
        async with self.lock:
            current_time = time.time()
            bucket_name, identifier = key.split(':', 2)
            
            if bucket_name == "rl:ip":
                bucket = self.ip_buckets[identifier]
            elif bucket_name == "rl:chat":  
                bucket = self.chat_buckets[identifier]
            else:
                return True, {"count": 0, "limit": limit}
                
            # Удаляем старые записи
            while bucket and bucket[0] <= current_time - window:
                bucket.popleft()
                
            # Проверяем лимит
            if len(bucket) >= limit:
                return False, {"count": len(bucket), "limit": limit, "retry_after": 1}
                
            # Добавляем новую запись
            bucket.append(current_time)
            return True, {"count": len(bucket), "limit": limit}
            
    async def check_duplicate(self, update_id: int, ttl: int) -> bool:
        """Проверка дубликатов"""
        async with self.lock:
            key = f"dup:tg:{update_id}"
            current_time = time.time()
            
            # Очистка старых записей
            expired_keys = [k for k, t in self.duplicates.items() 
                          if current_time - t > ttl]
            for k in expired_keys:
                del self.duplicates[k]
                
            # Проверка дубликата
            if key in self.duplicates:
                return True  # Дубликат найден
                
            # Записываем новый
            self.duplicates[key] = current_time
            return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware с Redis и in-memory fallback"""
    
    def __init__(self, app, config: RateLimitConfig, redis_url: Optional[str] = None):
        super().__init__(app)
        self.config = config
        self.redis_client: Optional[aioredis.Redis] = None
        self.fallback = InMemoryRateLimit()
        self.excluded_paths: Set[str] = {
            "GET:/health", "GET:/healthz", "GET:/metrics"
        }
        
        # Инициализация Redis (если доступен)
        if REDIS_AVAILABLE and redis_url:
            try:
                self.redis_client = aioredis.from_url(redis_url)
                logger.info(f"rate_limit_redis_initialized url={redis_url[:20]}***")
            except Exception as e:
                logger.warning(f"rate_limit_redis_failed error={str(e)}")
                
    async def _check_redis_rate_limit(self, key: str, limit: int, window: int = 1) -> tuple[bool, dict]:
        """Redis-based rate limiting"""
        if not self.redis_client:
            return await self.fallback.check_rate_limit(key, limit, window)
            
        try:
            current_time = int(time.time())
            
            pipe = self.redis_client.pipeline()
            pipe.zadd(key, {current_time: current_time})
            pipe.zremrangebyscore(key, 0, current_time - window)
            pipe.zcard(key)
            pipe.expire(key, window + 1)
            
            results = await pipe.execute()
            count = results[2]
            
            allowed = count <= limit
            metadata = {
                'count': count,
                'limit': limit,
                'retry_after': 1 if not allowed else 0
            }
            
            return allowed, metadata
            
        except Exception as e:
            logger.warning(f"redis_rate_limit_error error={str(e)} key={key[:20]}")
            return await self.fallback.check_rate_limit(key, limit, window)
            
    async def _check_redis_duplicate(self, update_id: int) -> bool:
        """Redis-based деду пликация"""
        if not self.redis_client:
            return await self.fallback.check_duplicate(update_id, self.config.dup_ttl)
            
        try:
            key = f"dup:tg:{update_id}"
            # SET NX EX - atomic set if not exists with expiration
            result = await self.redis_client.set(key, "1", ex=self.config.dup_ttl, nx=True)
            return result is None  # None means key already existed
            
        except Exception as e:
            logger.warning(f"redis_duplicate_error error={str(e)} update_id={update_id}")
            return await self.fallback.check_duplicate(update_id, self.config.dup_ttl)
            
    def _extract_chat_id_from_body(self, body: bytes) -> str:
        """Извлечение chat_id из тела Telegram update"""
        try:
            data = json.loads(body.decode('utf-8'))
            
            # Поиск chat_id в различных местах
            locations = [
                data.get('message', {}).get('chat', {}).get('id'),
                data.get('edited_message', {}).get('chat', {}).get('id'),
                data.get('callback_query', {}).get('message', {}).get('chat', {}).get('id'),
                data.get('channel_post', {}).get('chat', {}).get('id'),
                data.get('my_chat_member', {}).get('chat', {}).get('id')
            ]
            
            for chat_id in locations:
                if chat_id is not None:
                    return str(chat_id)
                    
        except (json.JSONDecodeError, AttributeError, KeyError):
            pass
            
        return "unknown"
        
    def _is_excluded_path(self, request: Request) -> bool:
        """Проверка исключенных путей"""
        path_key = f"{request.method}:{request.url.path}"
        return path_key in self.excluded_paths
        
    async def dispatch(self, request: Request, call_next):
        """Основная логика middleware"""
        # Пропускаем health endpoints
        if self._is_excluded_path(request):
            return await call_next(request)
            
        # Применяем только к webhook endpoints
        if not request.url.path.startswith("/telegram/"):
            return await call_next(request)
            
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        
        # Проверка размера тела запроса
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.config.body_limit:
            logger.warning(f"request_body_too_large ip={client_ip} size_bytes={int(content_length)} limit_bytes={self.config.body_limit}")
                          
            if PROMETHEUS_AVAILABLE:
                WEBHOOK_LIMITED.labels(type="body_size").inc()
                
            return JSONResponse(
                status_code=413,
                content={"detail": "payload_too_large"}
            )
            
        # Получение и проверка тела запроса
        try:
            body = await request.body()
        except Exception as e:
            logger.warning(f"request_body_read_error ip={client_ip} error={str(e)}")
            return JSONResponse(
                status_code=400,
                content={"detail": "invalid_request"}
            )
            
        # Дополнительная проверка размера после чтения
        if len(body) > self.config.body_limit:
            logger.warning(f"request_body_too_large_actual ip={client_ip} size_bytes={len(body)} limit_bytes={self.config.body_limit}")
                          
            if PROMETHEUS_AVAILABLE:
                WEBHOOK_LIMITED.labels(type="body_size").inc()
                
            return JSONResponse(
                status_code=413,
                content={"detail": "payload_too_large"}
            )
            
        # Извлечение данных из update
        update_id = None
        chat_id = "unknown"
        
        try:
            data = json.loads(body.decode('utf-8'))
            update_id = data.get('update_id')
            chat_id = self._extract_chat_id_from_body(body)
        except (json.JSONDecodeError, AttributeError):
            logger.warning(f"invalid_json_webhook ip={client_ip}")
            
        # Структурированное логирование входящего запроса
        log_data = {
            "event": "webhook.in",
            "ip": client_ip,
            "chat_id": chat_id,
            "update_id": update_id,
            "size": len(body),
            "limited": "no",
            "dup": False
        }
        
        # Проверка дедупликации
        is_duplicate = False
        if update_id:
            try:
                is_duplicate = await self._check_redis_duplicate(update_id)
                if is_duplicate:
                    log_data["dup"] = True
                    logger.info(f"webhook_duplicate_ignored {log_data}")
                    
                    if PROMETHEUS_AVAILABLE:
                        WEBHOOK_DUPLICATES.inc()
                        
                    return JSONResponse(
                        status_code=200,
                        content={"status": "ok", "message": "duplicate"}
                    )
            except Exception as e:
                logger.warning(f"duplicate_check_error error={str(e)} update_id={update_id}")
                
        # Проверка IP rate limit
        try:
            ip_allowed, ip_metadata = await self._check_redis_rate_limit(
                f"rl:ip:{client_ip}", 
                self.config.ip_burst
            )
            
            if not ip_allowed:
                log_data["limited"] = "ip"
                logger.warning(f"ip_rate_limit_exceeded {log_data} count={ip_metadata.get('count')} limit={ip_metadata.get('limit')}")
                              
                if PROMETHEUS_AVAILABLE:
                    WEBHOOK_LIMITED.labels(type="ip").inc()
                    
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate_limited"}
                )
        except Exception as e:
            logger.warning(f"ip_rate_limit_error error={str(e)} ip={client_ip}")
            
        # Проверка chat rate limit (если chat_id известен)
        if chat_id != "unknown":
            try:
                chat_allowed, chat_metadata = await self._check_redis_rate_limit(
                    f"rl:chat:{chat_id}",
                    self.config.chat_burst
                )
                
                if not chat_allowed:
                    log_data["limited"] = "chat"
                    logger.warning(f"chat_rate_limit_exceeded {log_data} count={chat_metadata.get('count')} limit={chat_metadata.get('limit')}")
                                  
                    if PROMETHEUS_AVAILABLE:
                        WEBHOOK_LIMITED.labels(type="chat").inc()
                        
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "rate_limited"}
                    )
            except Exception as e:
                logger.warning(f"chat_rate_limit_error error={str(e)} chat_id={chat_id}")
                
        # Восстановление тела запроса для дальнейшей обработки
        async def receive():
            return {"type": "http.request", "body": body}
            
        request._receive = receive
        
        # Логирование разрешенного запроса
        logger.info(f"webhook_request_allowed {log_data}")
        
        # Продолжение обработки
        response = await call_next(request)
        
        # Метрики времени обработки
        duration = time.time() - start_time
        logger.debug(f"webhook_request_completed ip={client_ip} chat_id={chat_id} update_id={update_id} duration_ms={int(duration * 1000)}")
                    
        return response


def setup_middlewares(
    app, 
    redis_url: Optional[str] = None,
    *,
    ip_rps: int = 10,
    ip_burst: int = 20, 
    chat_rps: int = 3,
    chat_burst: int = 6,
    body_limit: int = 2_097_152
) -> None:
    """
    Настройка rate limiting middleware
    
    Args:
        app: FastAPI приложение
        redis_url: URL Redis сервера (опционально)
        ip_rps: Requests per second per IP (не используется, burst важнее)
        ip_burst: Максимальное количество запросов в burst для IP
        chat_rps: Requests per second per chat (не используется)  
        chat_burst: Максимальное количество запросов в burst для чата
        body_limit: Максимальный размер тела запроса в байтах
    """
    try:
        config = RateLimitConfig(
            ip_rps=ip_rps,
            ip_burst=ip_burst,
            chat_rps=chat_rps, 
            chat_burst=chat_burst,
            body_limit=body_limit
        )
        
        middleware = RateLimitMiddleware(app, config, redis_url)
        app.add_middleware(RateLimitMiddleware, config=config, redis_url=redis_url)
        
        logger.info(f"rate_limit_middleware_setup_complete redis_available={REDIS_AVAILABLE} prometheus_available={PROMETHEUS_AVAILABLE} ip_burst={ip_burst} chat_burst={chat_burst} body_limit_mb={body_limit // 1024 // 1024}")
                   
    except Exception as e:
        logger.error(f"rate_limit_middleware_setup_failed error={str(e)}")
        raise