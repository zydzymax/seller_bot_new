"""
idempotency.py — Идемпотентность для Telegram webhook'ов и API.

- Предотвращение дубликатов обработки
- Redis кэширование результатов
- Configurable TTL для ключей
- Асинхронные операции

© SoVAni 2025
"""

import os
import json
import hashlib
from typing import Any, Optional, Callable, Awaitable
from dataclasses import dataclass
from enum import Enum

import redis.asyncio as redis
from utils.logging import get_logger

logger = get_logger(__name__)


class IdempotencyStatus(Enum):
    """Статусы идемпотентных операций"""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IdempotencyResult:
    """Результат проверки идемпотентности"""

    is_duplicate: bool
    status: IdempotencyStatus
    result: Any = None
    error: Optional[str] = None
    created_at: Optional[float] = None


class IdempotencyManager:
    """Менеджер идемпотентности операций"""

    def __init__(
        self,
        redis_client: redis.Redis,
        default_ttl: int = 3600,
        key_prefix: str = "idempotency",
    ):
        self.redis_client = redis_client
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix

    def _generate_key(self, operation: str, identifier: str) -> str:
        """Генерация ключа для операции"""
        # Хэширование для предотвращения коллизий и ограничения длины ключа
        key_data = f"{operation}:{identifier}"
        key_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        return f"{self.key_prefix}:{operation}:{key_hash}"

    async def check_duplicate(
        self, operation: str, identifier: str
    ) -> IdempotencyResult:
        """
        Проверка дубликата операции

        Args:
            operation: Тип операции (e.g., 'telegram_update', 'api_request')
            identifier: Уникальный идентификатор (e.g., update_id, request_id)

        Returns:
            IdempotencyResult с информацией о дубликате
        """
        key = self._generate_key(operation, identifier)

        try:
            cached_data = await self.redis_client.get(key)

            if not cached_data:
                return IdempotencyResult(
                    is_duplicate=False, status=IdempotencyStatus.PROCESSING
                )

            # Десериализация кэшированных данных
            data = json.loads(cached_data.decode("utf-8"))

            return IdempotencyResult(
                is_duplicate=True,
                status=IdempotencyStatus(data.get("status", "processing")),
                result=data.get("result"),
                error=data.get("error"),
                created_at=data.get("created_at"),
            )

        except Exception as e:
            logger.error(
                f"Error checking idempotency: {e}",
                operation=operation,
                identifier=identifier,
            )
            # В случае ошибки считаем что дубликата нет
            return IdempotencyResult(
                is_duplicate=False, status=IdempotencyStatus.PROCESSING
            )

    async def mark_processing(
        self, operation: str, identifier: str, ttl: Optional[int] = None
    ) -> bool:
        """
        Отметка начала обработки операции

        Args:
            operation: Тип операции
            identifier: Уникальный идентификатор
            ttl: TTL в секундах (по умолчанию default_ttl)

        Returns:
            True если успешно отмечено, False если уже обрабатывается
        """
        key = self._generate_key(operation, identifier)
        ttl = ttl or self.default_ttl

        try:
            # Используем SET NX для атомарной установки "только если не существует"
            data = {
                "status": IdempotencyStatus.PROCESSING.value,
                "created_at": __import__("time").time(),
            }

            result = await self.redis_client.set(
                key,
                json.dumps(data),
                ex=ttl,
                nx=True,  # Установить только если ключ не существует
            )

            success = bool(result)

            if success:
                logger.info(
                    "Marked operation as processing",
                    operation=operation,
                    identifier=identifier,
                )
            else:
                logger.warning(
                    "Operation already processing",
                    operation=operation,
                    identifier=identifier,
                )

            return success

        except Exception as e:
            logger.error(
                f"Error marking processing: {e}",
                operation=operation,
                identifier=identifier,
            )
            return False

    async def mark_completed(
        self,
        operation: str,
        identifier: str,
        result: Any = None,
        ttl: Optional[int] = None,
    ):
        """
        Отметка завершения операции с результатом

        Args:
            operation: Тип операции
            identifier: Уникальный идентификатор
            result: Результат операции
            ttl: TTL в секундах
        """
        key = self._generate_key(operation, identifier)
        ttl = ttl or self.default_ttl

        try:
            data = {
                "status": IdempotencyStatus.COMPLETED.value,
                "result": result,
                "completed_at": __import__("time").time(),
            }

            await self.redis_client.setex(key, ttl, json.dumps(data, default=str))

            logger.info(
                "Marked operation as completed",
                operation=operation,
                identifier=identifier,
            )

        except Exception as e:
            logger.error(
                f"Error marking completed: {e}",
                operation=operation,
                identifier=identifier,
            )

    async def mark_failed(
        self, operation: str, identifier: str, error: str, ttl: Optional[int] = None
    ):
        """
        Отметка неудачи операции с ошибкой

        Args:
            operation: Тип операции
            identifier: Уникальный идентификатор
            error: Описание ошибки
            ttl: TTL в секундах
        """
        key = self._generate_key(operation, identifier)
        ttl = ttl or self.default_ttl

        try:
            data = {
                "status": IdempotencyStatus.FAILED.value,
                "error": str(error),
                "failed_at": __import__("time").time(),
            }

            await self.redis_client.setex(key, ttl, json.dumps(data))

            logger.warning(
                "Marked operation as failed",
                operation=operation,
                identifier=identifier,
                error=error,
            )

        except Exception as e:
            logger.error(
                f"Error marking failed: {e}", operation=operation, identifier=identifier
            )

    async def clear_operation(self, operation: str, identifier: str):
        """Очистка записи об операции"""
        key = self._generate_key(operation, identifier)

        try:
            await self.redis_client.delete(key)
            logger.info(
                "Cleared operation", operation=operation, identifier=identifier
            )
        except Exception as e:
            logger.error(
                f"Error clearing operation: {e}",
                operation=operation,
                identifier=identifier,
            )

    async def execute_idempotent(
        self,
        operation: str,
        identifier: str,
        handler: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Выполнение идемпотентной операции

        Args:
            operation: Тип операции
            identifier: Уникальный идентификатор
            handler: Асинхронная функция-обработчик
            ttl: TTL для кэширования результата

        Returns:
            Результат выполнения операции
        """
        # Проверка дубликата
        check_result = await self.check_duplicate(operation, identifier)

        if check_result.is_duplicate:
            if check_result.status == IdempotencyStatus.COMPLETED:
                logger.info(
                    "Returning cached result",
                    operation=operation,
                    identifier=identifier,
                )
                return check_result.result
            elif check_result.status == IdempotencyStatus.FAILED:
                logger.warning(
                    "Operation previously failed",
                    operation=operation,
                    identifier=identifier,
                )
                raise Exception(f"Previous operation failed: {check_result.error}")
            else:
                # Операция еще обрабатывается
                logger.info(
                    "Operation already processing",
                    operation=operation,
                    identifier=identifier,
                )
                return None

        # Отметка начала обработки
        if not await self.mark_processing(operation, identifier, ttl):
            # Другой процесс уже начал обработку
            return None

        try:
            # Выполнение операции
            result = await handler()

            # Сохранение результата
            await self.mark_completed(operation, identifier, result, ttl)

            return result

        except Exception as e:
            # Сохранение ошибки
            await self.mark_failed(operation, identifier, str(e), ttl)
            raise

    async def health_check(self) -> dict:
        """Проверка состояния менеджера идемпотентности"""
        try:
            # Тестовая операция
            test_key = f"{self.key_prefix}:health_check"
            await self.redis_client.setex(test_key, 1, "test")
            await self.redis_client.delete(test_key)

            return {
                "status": "healthy",
                "redis_connected": True,
                "default_ttl": self.default_ttl,
            }
        except Exception as e:
            return {"status": "unhealthy", "redis_connected": False, "error": str(e)}


# Глобальный экземпляр менеджера
_idempotency_manager = None


async def get_idempotency_manager(redis_url: str = None) -> IdempotencyManager:
    """Получение singleton экземпляра менеджера идемпотентности"""
    global _idempotency_manager

    if _idempotency_manager is None:
        redis_url = redis_url or os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
        redis_client = redis.from_url(redis_url)
        ttl = int(os.getenv("IDEMPOTENCY_TTL", "3600"))
        _idempotency_manager = IdempotencyManager(redis_client, ttl)

    return _idempotency_manager


class IdempotencyMiddleware:
    """Middleware для автоматической обработки идемпотентности"""

    def __init__(
        self,
        idempotency_manager: IdempotencyManager,
        operation_name: str = "api_request",
    ):
        self.idempotency_manager = idempotency_manager
        self.operation_name = operation_name

    def get_identifier(self, request) -> Optional[str]:
        """
        Извлечение идентификатора из запроса

        Для Telegram: использует update_id
        Для HTTP API: использует заголовок Idempotency-Key или request_id
        """
        # Telegram update
        if hasattr(request, "update_id"):
            return str(request.update_id)

        # HTTP request с заголовком
        if hasattr(request, "headers"):
            idempotency_key = request.headers.get(
                "Idempotency-Key"
            ) or request.headers.get("X-Idempotency-Key")
            if idempotency_key:
                return idempotency_key

            # Можно использовать hash от тела запроса для POST
            if hasattr(request, "body") and request.method in ["POST", "PUT", "PATCH"]:
                content_hash = hashlib.md5(request.body).hexdigest()
                return f"{request.method}:{request.url}:{content_hash}"

        return None

    async def __call__(self, request, call_next=None):
        """Middleware обработчик"""
        identifier = self.get_identifier(request)

        if not identifier:
            # Если нет идентификатора, пропускаем идемпотентность
            if call_next:
                return await call_next(request)
            return None

        # Проверка дубликата
        check_result = await self.idempotency_manager.check_duplicate(
            self.operation_name, identifier
        )

        if check_result.is_duplicate:
            if check_result.status == IdempotencyStatus.COMPLETED:
                logger.info(
                    "Returning cached response",
                    operation=self.operation_name,
                    identifier=identifier,
                )
                return check_result.result
            elif check_result.status == IdempotencyStatus.FAILED:
                logger.warning(
                    "Previous request failed",
                    operation=self.operation_name,
                    identifier=identifier,
                )
                return {
                    "error": "Previous request failed",
                    "message": check_result.error,
                }
            else:
                # Запрос уже обрабатывается
                logger.info(
                    "Request already processing",
                    operation=self.operation_name,
                    identifier=identifier,
                )
                return {
                    "error": "Request already processing",
                    "message": "This request is currently being processed",
                }

        # Отметка начала обработки
        if not await self.idempotency_manager.mark_processing(
            self.operation_name, identifier
        ):
            return {
                "error": "Concurrent processing",
                "message": "Another process is handling this request",
            }

        try:
            # Выполнение оригинального обработчика
            if call_next:
                result = await call_next(request)
            else:
                result = None

            # Сохранение результата
            await self.idempotency_manager.mark_completed(
                self.operation_name, identifier, result
            )

            return result

        except Exception as e:
            # Сохранение ошибки
            await self.idempotency_manager.mark_failed(
                self.operation_name, identifier, str(e)
            )
            raise


# Декоратор для идемпотентных функций
def idempotent(operation: str, identifier_func: Callable = None, ttl: int = None):
    """
    Декоратор для создания идемпотентных функций

    Args:
        operation: Название операции
        identifier_func: Функция для извлечения идентификатора из аргументов
        ttl: TTL для кэширования результата
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Получение менеджера идемпотентности
            manager = await get_idempotency_manager()

            # Генерация идентификатора
            if identifier_func:
                identifier = identifier_func(*args, **kwargs)
            else:
                # По умолчанию используем hash от аргументов
                args_str = json.dumps((args, kwargs), default=str, sort_keys=True)
                identifier = hashlib.md5(args_str.encode()).hexdigest()

            # Выполнение идемпотентной операции
            async def handler():
                return await func(*args, **kwargs)

            return await manager.execute_idempotent(operation, identifier, handler, ttl)

        return wrapper

    return decorator


__all__ = [
    "IdempotencyManager",
    "IdempotencyResult",
    "IdempotencyStatus",
    "IdempotencyMiddleware",
    "get_idempotency_manager",
    "idempotent",
]
