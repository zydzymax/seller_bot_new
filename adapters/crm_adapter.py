"""
crm_adapter.py — CRM интеграция для SoVAni AI-продавца.

Интеграция с AmoCRM через REST API:
- Создание и обновление лидов
- DLQ для неуспешных запросов
- Идемпотентность через Redis кэширование
- Детальное логирование операций

© SoVAni 2025
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum

import aiohttp
import redis.asyncio as aioredis
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ContactData:
    """Данные контакта клиента"""

    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    telegram_username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


@dataclass
class OrderDetails:
    """Детали заказа"""

    product_type: Optional[str] = None
    total_quantity: Optional[int] = None
    colors_count: Optional[int] = None
    fabric_composition: Optional[str] = None
    special_requirements: Optional[str] = None
    estimated_value: Optional[float] = None
    delivery_timeline: Optional[str] = None
    pricing_mode: Optional[str] = None
    order_summary: Optional[str] = None


class LeadStatus(str, Enum):
    """Нормализованный статус результата CRM операции."""

    SUCCESS = "success"
    CACHED = "cached"
    ERROR = "error"


class CRMAdapter:
    """Адаптер для работы с AmoCRM"""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis_client = redis_client
        self.amocrm_domain = os.getenv("AMOCRM_DOMAIN", "example.amocrm.ru")
        self.api_token = os.getenv("AMOCRM_API_TOKEN", "")
        self.user_hash = os.getenv("AMOCRM_USER_HASH", "")

        # Настройки retry
        self.max_retries = 3
        self.retry_delay = 1.0

        # DLQ для неуспешных запросов
        self.dlq_key = "crm:dlq"

        logger.info(
            "CRM Adapter инициализирован",
            domain=self.amocrm_domain,
            has_token=bool(self.api_token),
        )

    async def create_or_update_lead(
        self,
        external_id: str,
        contact_data: ContactData,
        order_details: OrderDetails,
        source: str = "telegram_bot",
    ) -> Dict[str, Any]:
        """Создание или обновление лида"""
        operation_id = f"lead_create_{external_id}_{int(datetime.now().timestamp())}"

        try:
            # Проверка идемпотентности через кэш в Redis
            cache_key = f"crm:processed:{external_id}"
            cached_result = await self.redis_client.get(cache_key)

            if cached_result:
                cached_data = json.loads(cached_result)
                logger.info(
                    "Lead уже обработан",
                    external_id=external_id,
                    cached_result=cached_result.decode(),
                )
                return {
                    "success": True,
                    "status": LeadStatus.CACHED.value,
                    "cached": True,
                    **cached_data,
                }

            # Создание контакта
            contact_id = await self._create_or_find_contact(contact_data)
            if not contact_id:
                raise Exception("Не удалось создать контакт")

            # Создание лида
            lead_id = await self._create_lead(contact_id, order_details, source)
            if not lead_id:
                raise Exception("Не удалось создать лид")

            result = {
                "success": True,
                "status": LeadStatus.SUCCESS.value,
                "contact_id": contact_id,
                "lead_id": lead_id,
                "external_id": external_id,
                "created_at": datetime.now().isoformat(),
            }

            # Кэширование результата
            await self.redis_client.setex(
                cache_key, timedelta(hours=24), json.dumps(result)
            )

            logger.info(
                "Lead успешно создан",
                operation_id=operation_id,
                contact_id=contact_id,
                lead_id=lead_id,
            )

            return result

        except Exception as e:
            logger.error(
                "Ошибка создания lead",
                operation_id=operation_id,
                error=str(e),
                external_id=external_id,
            )

            # Добавление в DLQ для повторной обработки
            await self._add_to_dlq(
                {
                    "operation_id": operation_id,
                    "external_id": external_id,
                    "contact_data": asdict(contact_data),
                    "order_details": asdict(order_details),
                    "source": source,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                    "retry_count": 0,
                }
            )

            return {
                "success": False,
                "status": LeadStatus.ERROR.value,
                "error": str(e),
            }

    async def _create_or_find_contact(self, contact_data: ContactData) -> Optional[int]:
        """Создание контакта (заглушка)"""
        try:
            # Имитация создания контакта в AmoCRM
            contact_fields = {
                "name": contact_data.name or "Новый контакт",
                "custom_fields": [],
            }

            if contact_data.phone:
                contact_fields["custom_fields"].append(
                    {
                        "field_id": 123,  # ID поля телефона
                        "values": [{"value": contact_data.phone, "enum_id": 456}],
                    }
                )

            if contact_data.email:
                contact_fields["custom_fields"].append(
                    {
                        "field_id": 124,  # ID поля email
                        "values": [{"value": contact_data.email, "enum_id": 457}],
                    }
                )

            # В реальной реализации здесь был бы HTTP запрос к AmoCRM API
            # contact_response = await self._make_api_request("POST", "/api/v4/contacts", [contact_fields])

            # Заглушка - возвращаем случайный ID
            contact_id = 12345

            logger.info(
                "Контакт создан (заглушка)",
                contact_id=contact_id,
                name=contact_data.name,
            )

            return contact_id

        except Exception as e:
            logger.error(
                "Ошибка создания контакта",
                error=str(e),
                contact_data=asdict(contact_data),
            )
            return None

    async def _create_lead(
        self, contact_id: int, order_details: OrderDetails, source: str = "ai_bot"
    ) -> Optional[int]:
        """Создание лида (заглушка)"""
        try:
            # Формирование данных лида
            lead_data = []

            lead_fields = {
                "name": f"Заказ AI-продавца: {order_details.product_type or 'Заказ'}",
                "status_id": 123,  # ID статуса "Первичный контакт"
                "pipeline_id": 456,  # ID воронки
                "contacts_id": [contact_id],
            }

            # Установка стоимости если есть
            if order_details.estimated_value:
                lead_fields["price"] = int(order_details.estimated_value)

            # Добавление кастомных полей
            custom_fields = []

            if order_details.product_type:
                custom_fields.append(
                    {
                        "field_id": 789,  # ID поля типа продукта
                        "values": [{"value": order_details.product_type}],
                    }
                )

            if order_details.total_quantity:
                custom_fields.append(
                    {
                        "field_id": 790,  # ID поля количества
                        "values": [{"value": str(order_details.total_quantity)}],
                    }
                )

            if order_details.order_summary:
                custom_fields.append(
                    {
                        "field_id": 791,  # ID поля описания
                        "values": [{"value": order_details.order_summary}],
                    }
                )

            if custom_fields:
                lead_fields["custom_fields"] = custom_fields

            lead_data.append(lead_fields)

            # В реальной реализации здесь был бы HTTP запрос к AmoCRM API
            # lead_response = await self._make_api_request("POST", "/api/v4/leads", lead_data)

            # Заглушка - возвращаем случайный ID
            lead_id = 67890

            logger.info(
                "Лид создан (заглушка)",
                lead_id=lead_id,
                contact_id=contact_id,
                product_type=order_details.product_type,
            )

            return lead_id

        except Exception as e:
            logger.error(
                "Ошибка создания лида",
                error=str(e),
                contact_id=contact_id,
                order_details=asdict(order_details),
            )
            return None

    async def _make_api_request(
        self, method: str, endpoint: str, data: Any = None
    ) -> Dict[str, Any]:
        """HTTP запрос к AmoCRM API с retry логикой"""
        url = f"https://{self.amocrm_domain}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as session:
                    async with session.request(
                        method, url, headers=headers, json=data if data else None
                    ) as response:

                        if response.status == 200 or response.status == 201:
                            result = await response.json()
                            logger.info(
                                "API запрос успешен",
                                method=method,
                                endpoint=endpoint,
                                status=response.status,
                            )
                            return result
                        elif response.status == 429:  # Rate limit
                            retry_after = int(
                                response.headers.get("Retry-After", self.retry_delay)
                            )
                            logger.warning(
                                "Rate limit, ожидание",
                                retry_after=retry_after,
                                attempt=attempt + 1,
                            )
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            error_text = await response.text()
                            raise Exception(f"HTTP {response.status}: {error_text}")

            except asyncio.TimeoutError:
                logger.warning(
                    "Таймаут API запроса",
                    attempt=attempt + 1,
                    method=method,
                    endpoint=endpoint,
                )
            except Exception as e:
                logger.error(
                    "Ошибка API запроса",
                    error=str(e),
                    attempt=attempt + 1,
                    method=method,
                    endpoint=endpoint,
                )

                if attempt == self.max_retries - 1:
                    raise

                await asyncio.sleep(self.retry_delay * (2**attempt))

        raise Exception(f"API запрос неуспешен после {self.max_retries} попыток")

    async def _add_to_dlq(self, failed_operation: Dict[str, Any]) -> None:
        """Добавление неуспешной операции в DLQ"""
        try:
            await self.redis_client.lpush(self.dlq_key, json.dumps(failed_operation))

            logger.info(
                "Операция добавлена в DLQ",
                operation_id=failed_operation.get("operation_id"),
                error=failed_operation.get("error"),
            )

        except Exception as e:
            logger.error(
                "Ошибка добавления в DLQ",
                error=str(e),
                operation_id=failed_operation.get("operation_id"),
            )

    async def process_dlq(self, batch_size: int = 10) -> Dict[str, Any]:
        """Обработка операций из DLQ"""
        processed = 0
        errors = 0

        try:
            for _ in range(batch_size):
                # Получение операции из DLQ
                dlq_item = await self.redis_client.rpop(self.dlq_key)
                if not dlq_item:
                    break

                try:
                    operation = json.loads(dlq_item)
                    retry_count = operation.get("retry_count", 0)

                    # Максимум 3 повторных попытки
                    if retry_count >= 3:
                        logger.warning(
                            "Операция превысила максимум попыток",
                            operation_id=operation.get("operation_id"),
                        )
                        errors += 1
                        continue

                    # Повторная попытка обработки
                    contact_data = ContactData(**operation["contact_data"])
                    order_details = OrderDetails(**operation["order_details"])

                    result = await self.create_or_update_lead(
                        operation["external_id"],
                        contact_data,
                        order_details,
                        operation["source"],
                    )

                    if result.get("status") in {
                        LeadStatus.SUCCESS.value,
                        LeadStatus.CACHED.value,
                    } or result.get("success"):
                        processed += 1
                        logger.info(
                            "DLQ операция успешно обработана",
                            operation_id=operation.get("operation_id"),
                        )
                    else:
                        # Возвращаем в DLQ с увеличенным счетчиком
                        operation["retry_count"] = retry_count + 1
                        await self._add_to_dlq(operation)
                        errors += 1

                except json.JSONDecodeError:
                    logger.error("Некорректный JSON в DLQ")
                    errors += 1
                except Exception as e:
                    logger.error("Ошибка обработки DLQ операции", error=str(e))
                    errors += 1

            logger.info("DLQ обработка завершена", processed=processed, errors=errors)

            return {
                "processed": processed,
                "errors": errors,
                "remaining": await self.redis_client.llen(self.dlq_key),
            }

        except Exception as e:
            logger.error("Критическая ошибка обработки DLQ", error=str(e))
            return {"error": str(e)}

    async def get_health_status(self) -> Dict[str, Any]:
        """Проверка состояния CRM интеграции"""
        try:
            # Проверка соединения с Redis
            await self.redis_client.ping()
            redis_status = "healthy"
        except Exception as e:
            redis_status = f"unhealthy: {str(e)}"

        # Проверка конфигурации
        config_status = "healthy" if self.api_token else "missing_token"

        # Размер DLQ
        try:
            dlq_size = await self.redis_client.llen(self.dlq_key)
        except Exception:
            dlq_size = -1

        return {
            "status": (
                "healthy"
                if redis_status == "healthy" and config_status == "healthy"
                else "degraded"
            ),
            "redis": redis_status,
            "config": config_status,
            "dlq_size": dlq_size,
            "domain": self.amocrm_domain,
            "last_check": datetime.now().isoformat(),
        }


# Singleton instance
_crm_adapter_instance: Optional[CRMAdapter] = None


async def get_crm_adapter(redis_client: aioredis.Redis = None) -> CRMAdapter:
    """Получение экземпляра CRM адаптера (singleton)"""
    global _crm_adapter_instance

    if _crm_adapter_instance is None:
        if redis_client is None:
            # Создание Redis клиента если не передан
            redis_url = os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
            redis_client = aioredis.from_url(redis_url)

        _crm_adapter_instance = CRMAdapter(redis_client)
        logger.info("CRM Adapter singleton создан")

    return _crm_adapter_instance
