"""
crm_adapter.py — CRM адаптер для SoVAni AI-продавца (AmoCRM stub).

- Async HTTP клиент с ретраями и таймаутами
- Идемпотентность по external_id (chat_id:session_id)
- DLQ для неуспешных запросов в Redis
- Интеграция с DialogContext данными
- Prometheus метрики и health checks

© SoVAni 2025
"""

import os
import json
import time
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

import httpx
import redis.asyncio as redis
from pydantic import BaseModel

from utils.logging import get_logger

logger = get_logger(__name__)


class LeadStatus(Enum):
    """Статусы лидов в CRM"""
    NEW = "new"
    QUALIFIED = "qualified"  
    PROPOSAL_SENT = "proposal_sent"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


@dataclass
class ContactData:
    """Контактные данные клиента"""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    
    
@dataclass  
class OrderDetails:
    """Детали заказа"""
    product_type: Optional[str] = None
    total_quantity: Optional[int] = None
    colors_count: Optional[int] = None
    fabric_composition: Optional[str] = None
    pricing_mode: Optional[str] = None
    estimated_value: Optional[float] = None


class CRMAdapter:
    """Адаптер для интеграции с CRM системами"""
    
    def __init__(self, redis_url: str = None):
        # Настройки CRM (AmoCRM в данном случае)
        self.crm_domain = os.getenv('AMOCRM_DOMAIN', 'your-domain.amocrm.ru')
        self.api_token = os.getenv('AMOCRM_API_TOKEN', '')
        self.user_hash = os.getenv('AMOCRM_USER_HASH', '')
        
        # Redis для DLQ и кэширования
        self.redis_client = redis.from_url(redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0'))
        
        # HTTP клиент с настройками
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0),
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'SoVAni-AI-Seller/1.0'
            }
        )
        
        # Настройки ретраев
        self.max_retries = int(os.getenv('CRM_MAX_RETRIES', '3'))
        self.retry_delay = float(os.getenv('CRM_RETRY_DELAY', '1.0'))
        
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None,
        retry_count: int = 0
    ) -> Dict[str, Any]:
        """Выполнение HTTP запроса к CRM API с ретраями"""
        url = f"https://{self.crm_domain}/api/v4/{endpoint}"
        
        try:
            if method.upper() == 'GET':
                response = await self.http_client.get(url, params=data)
            else:
                response = await self.http_client.request(method, url, json=data)
                
            if response.status_code == 429:  # Rate limit
                if retry_count < self.max_retries:
                    delay = self.retry_delay * (2 ** retry_count)  # Exponential backoff
                    logger.warning(f"Rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    return await self._make_request(method, endpoint, data, retry_count + 1)
                else:
                    raise Exception(f"Rate limit exceeded after {self.max_retries} retries")
                    
            if response.status_code >= 500:  # Server errors
                if retry_count < self.max_retries:
                    delay = self.retry_delay * (2 ** retry_count)
                    logger.warning(f"Server error {response.status_code}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    return await self._make_request(method, endpoint, data, retry_count + 1)
                    
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            if retry_count < self.max_retries and not isinstance(e, httpx.HTTPStatusError):
                delay = self.retry_delay * (2 ** retry_count)
                logger.warning(f"Request failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
                return await self._make_request(method, endpoint, data, retry_count + 1)
            else:
                logger.error(f"CRM request failed: {method} {endpoint} - {e}")
                raise
                
    async def _add_to_dlq(self, operation: str, data: Dict[str, Any], error: str):
        """Добавление неуспешного запроса в DLQ"""
        dlq_item = {
            "operation": operation,
            "data": data,
            "error": str(error),
            "timestamp": time.time(),
            "retry_count": 0
        }
        
        try:
            await self.redis_client.lpush(
                "crm:dlq",
                json.dumps(dlq_item, default=str)
            )
            # Ограничиваем размер DLQ
            await self.redis_client.ltrim("crm:dlq", 0, 999)
            logger.info(f"Added failed CRM operation to DLQ: {operation}")
        except Exception as e:
            logger.error(f"Failed to add to DLQ: {e}")
            
    async def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Поиск контакта по номеру телефона"""
        if not phone:
            return None
            
        try:
            # Stub implementation - возвращаем заглушку
            logger.info(f"Searching contact by phone: {phone[:3]}***{phone[-2:]}")
            
            # В реальной реализации здесь будет вызов AmoCRM API
            # response = await self._make_request('GET', 'contacts', {'query': phone})
            
            # Пока возвращаем None (контакт не найден)
            return None
            
        except Exception as e:
            logger.error(f"Failed to search contact: {e}")
            await self._add_to_dlq("find_contact", {"phone": phone}, str(e))
            return None
            
    async def create_contact(self, contact_data: ContactData) -> Optional[int]:
        """Создание нового контакта"""
        try:
            crm_contact = []
            
            # Формирование данных контакта для AmoCRM
            contact_fields = {}
            
            if contact_data.name:
                contact_fields["name"] = contact_data.name
                
            # Custom fields (нужно знать ID полей в AmoCRM)
            custom_fields = []
            
            if contact_data.phone:
                custom_fields.append({
                    "field_id": 123456,  # ID поля телефона в AmoCRM
                    "values": [{"value": contact_data.phone, "enum_code": "WORK"}]
                })
                
            if contact_data.email:
                custom_fields.append({
                    "field_id": 123457,  # ID поля email в AmoCRM  
                    "values": [{"value": contact_data.email, "enum_code": "WORK"}]
                })
                
            if contact_data.company:
                contact_fields["company_name"] = contact_data.company
                
            if custom_fields:
                contact_fields["custom_fields_values"] = custom_fields
                
            crm_contact.append(contact_fields)
            
            # Stub implementation - логируем что создали бы контакт
            logger.info(f"Would create CRM contact", extra={
                "contact_name": contact_data.name,
                "has_phone": bool(contact_data.phone),
                "has_email": bool(contact_data.email),
                "company": contact_data.company
            })
            
            # В реальной реализации:
            # response = await self._make_request('POST', 'contacts', crm_contact)
            # return response['_embedded']['contacts'][0]['id']
            
            # Возвращаем mock ID
            return 12345
            
        except Exception as e:
            logger.error(f"Failed to create contact: {e}")
            await self._add_to_dlq("create_contact", contact_data.__dict__, str(e))
            return None
            
    async def create_lead(
        self, 
        contact_id: int,
        order_details: OrderDetails,
        external_id: str,
        source: str = "ai_bot"
    ) -> Optional[int]:
        """Создание лида (сделки)"""
        try:
            # Формирование данных лида
            lead_data = []
            
            lead_fields = {
                "name": f"Заказ от AI-бота: {order_details.product_type or 'Текстиль'}",
                "status_id": 123,  # ID статуса "Первичный контакт"
                "pipeline_id": 456,  # ID воронки
                "contacts_id": [contact_id]
            }
            
            # Установка стоимости если есть
            if order_details.estimated_value:
                lead_fields["price"] = int(order_details.estimated_value)
                
            # Custom fields для деталей заказа
            custom_fields = []
            
            if order_details.total_quantity:
                custom_fields.append({
                    "field_id": 789012,  # ID поля "Количество"
                    "values": [{"value": str(order_details.total_quantity)}]
                })
                
            if order_details.colors_count:
                custom_fields.append({
                    "field_id": 789013,  # ID поля "Количество цветов"
                    "values": [{"value": str(order_details.colors_count)}]
                })
                
            if order_details.fabric_composition:
                custom_fields.append({
                    "field_id": 789014,  # ID поля "Состав ткани"
                    "values": [{"value": order_details.fabric_composition}]
                })
                
            if order_details.pricing_mode:
                custom_fields.append({
                    "field_id": 789015,  # ID поля "Режим ценообразования"
                    "values": [{"value": order_details.pricing_mode}]
                })
                
            # Внешний ID для идемпотентности
            custom_fields.append({
                "field_id": 789016,  # ID поля "External ID"
                "values": [{"value": external_id}]
            })
            
            if custom_fields:
                lead_fields["custom_fields_values"] = custom_fields
                \n            lead_data.append(lead_fields)\n            \n            # Stub implementation\n            logger.info(f\"Would create CRM lead\", extra={\n                \"external_id\": external_id,\n                \"contact_id\": contact_id,\n                \"product_type\": order_details.product_type,\n                \"total_quantity\": order_details.total_quantity,\n                \"estimated_value\": order_details.estimated_value,\n                \"source\": source\n            })\n            \n            # В реальной реализации:\n            # response = await self._make_request('POST', 'leads', lead_data)\n            # return response['_embedded']['leads'][0]['id']\n            \n            # Возвращаем mock ID\n            return 67890\n            \n        except Exception as e:\n            logger.error(f\"Failed to create lead: {e}\")\n            await self._add_to_dlq(\"create_lead\", {\n                \"contact_id\": contact_id,\n                \"order_details\": order_details.__dict__,\n                \"external_id\": external_id,\n                \"source\": source\n            }, str(e))\n            return None\n            \n    async def update_lead(\n        self, \n        lead_id: int, \n        updates: Dict[str, Any]\n    ) -> bool:\n        \"\"\"Обновление существующего лида\"\"\"\n        try:\n            # Stub implementation\n            logger.info(f\"Would update CRM lead {lead_id}\", extra={\n                \"lead_id\": lead_id,\n                \"updates\": updates\n            })\n            \n            # В реальной реализации:\n            # response = await self._make_request('PATCH', f'leads/{lead_id}', updates)\n            # return True\n            \n            return True\n            \n        except Exception as e:\n            logger.error(f\"Failed to update lead {lead_id}: {e}\")\n            await self._add_to_dlq(\"update_lead\", {\n                \"lead_id\": lead_id,\n                \"updates\": updates\n            }, str(e))\n            return False\n            \n    async def create_or_update_lead(\n        self,\n        external_id: str,\n        contact_data: ContactData,\n        order_details: OrderDetails,\n        source: str = \"telegram_bot\"\n    ) -> Dict[str, Any]:\n        \"\"\"\n        Создание или обновление лида с полным pipeline\n        \n        Args:\n            external_id: Внешний идентификатор (например, tg_chat_id)\n            contact_data: Контактные данные\n            order_details: Детали заказа\n            source: Источник лида\n            \n        Returns:\n            Результат операции с ID созданных/обновленных записей\n        \"\"\"\n        result = {\n            \"success\": False,\n            \"contact_id\": None,\n            \"lead_id\": None,\n            \"operation\": \"unknown\",\n            \"external_id\": external_id\n        }\n        \n        try:\n            # Проверка идемпотентности через кэш в Redis\n            cache_key = f\"crm:processed:{external_id}\"\n            cached_result = await self.redis_client.get(cache_key)\n            \n            if cached_result:\n                logger.info(f\"Returning cached CRM result for {external_id}\")\n                return json.loads(cached_result.decode('utf-8'))\n                \n            # 1. Поиск существующего контакта по телефону\n            contact_id = None\n            if contact_data.phone:\n                existing_contact = await self.find_contact_by_phone(contact_data.phone)\n                if existing_contact:\n                    contact_id = existing_contact.get('id')\n                    result[\"operation\"] = \"found_existing_contact\"\n                    \n            # 2. Создание контакта если не найден\n            if not contact_id:\n                contact_id = await self.create_contact(contact_data)\n                if contact_id:\n                    result[\"operation\"] = \"created_contact\"\n                else:\n                    logger.error(\"Failed to create contact\")\n                    return result\n                    \n            result[\"contact_id\"] = contact_id\n            \n            # 3. Создание лида\n            lead_id = await self.create_lead(\n                contact_id=contact_id,\n                order_details=order_details,\n                external_id=external_id,\n                source=source\n            )\n            \n            if lead_id:\n                result[\"lead_id\"] = lead_id\n                result[\"success\"] = True\n                result[\"operation\"] += \"_and_created_lead\"\n                \n                # Кэширование результата на 1 час\n                await self.redis_client.setex(\n                    cache_key,\n                    3600,\n                    json.dumps(result, default=str)\n                )\n                \n                logger.info(f\"Successfully processed CRM lead\", extra={\n                    \"external_id\": external_id,\n                    \"contact_id\": contact_id,\n                    \"lead_id\": lead_id,\n                    \"operation\": result[\"operation\"]\n                })\n            else:\n                logger.error(\"Failed to create lead\")\n                \n        except Exception as e:\n            logger.error(f\"CRM processing failed for {external_id}: {e}\")\n            await self._add_to_dlq(\"create_or_update_lead\", {\n                \"external_id\": external_id,\n                \"contact_data\": contact_data.__dict__,\n                \"order_details\": order_details.__dict__,\n                \"source\": source\n            }, str(e))\n            \n        return result\n        \n    async def process_dlq_items(self, batch_size: int = 10) -> Dict[str, Any]:\n        \"\"\"Обработка элементов из DLQ\"\"\"\n        processed = 0\n        successful = 0\n        failed = 0\n        \n        try:\n            # Получение элементов из DLQ\n            items = await self.redis_client.lrange(\"crm:dlq\", 0, batch_size - 1)\n            \n            for item_data in items:\n                try:\n                    item = json.loads(item_data.decode('utf-8'))\n                    \n                    # Пропускаем элементы которые слишком старые (> 24 часов)\n                    if time.time() - item.get('timestamp', 0) > 86400:\n                        await self.redis_client.lrem(\"crm:dlq\", 1, item_data)\n                        continue\n                        \n                    # Пропускаем элементы с большим количеством попыток\n                    if item.get('retry_count', 0) >= 5:\n                        await self.redis_client.lrem(\"crm:dlq\", 1, item_data)\n                        failed += 1\n                        continue\n                        \n                    # Попытка повторной обработки\n                    operation = item.get('operation')\n                    data = item.get('data', {})\n                    \n                    success = False\n                    \n                    if operation == \"create_or_update_lead\":\n                        result = await self.create_or_update_lead(\n                            external_id=data.get('external_id'),\n                            contact_data=ContactData(**data.get('contact_data', {})),\n                            order_details=OrderDetails(**data.get('order_details', {})),\n                            source=data.get('source', 'dlq_retry')\n                        )\n                        success = result.get('success', False)\n                        \n                    if success:\n                        await self.redis_client.lrem(\"crm:dlq\", 1, item_data)\n                        successful += 1\n                        logger.info(f\"Successfully processed DLQ item: {operation}\")\n                    else:\n                        # Увеличиваем счётчик попыток\n                        item['retry_count'] = item.get('retry_count', 0) + 1\n                        await self.redis_client.lset(\"crm:dlq\", processed, json.dumps(item))\n                        failed += 1\n                        \n                    processed += 1\n                    \n                except Exception as e:\n                    logger.error(f\"Error processing DLQ item: {e}\")\n                    failed += 1\n                    processed += 1\n                    \n        except Exception as e:\n            logger.error(f\"DLQ processing error: {e}\")\n            \n        return {\n            \"processed\": processed,\n            \"successful\": successful,\n            \"failed\": failed\n        }\n        \n    async def health_check(self) -> Dict[str, Any]:\n        \"\"\"Проверка состояния CRM адаптера\"\"\"\n        health = {\n            \"status\": \"healthy\",\n            \"crm_configured\": bool(self.api_token),\n            \"redis_connected\": False,\n            \"dlq_size\": 0\n        }\n        \n        try:\n            # Проверка Redis\n            await self.redis_client.ping()\n            health[\"redis_connected\"] = True\n            \n            # Размер DLQ\n            dlq_size = await self.redis_client.llen(\"crm:dlq\")\n            health[\"dlq_size\"] = dlq_size\n            \n            if dlq_size > 100:\n                health[\"status\"] = \"degraded\"\n                health[\"warning\"] = f\"Large DLQ size: {dlq_size}\"\n                \n        except Exception as e:\n            health[\"status\"] = \"unhealthy\"\n            health[\"error\"] = str(e)\n            \n        return health\n        \n    async def close(self):\n        \"\"\"Закрытие соединений\"\"\"\n        try:\n            await self.http_client.aclose()\n            await self.redis_client.close()\n        except Exception as e:\n            logger.error(f\"Error closing CRM adapter: {e}\")\n\n\n# Singleton instance\n_crm_adapter_instance = None\n\n\nasync def get_crm_adapter(redis_url: str = None) -> CRMAdapter:\n    \"\"\"Получение singleton экземпляра CRM адаптера\"\"\"\n    global _crm_adapter_instance\n    \n    if _crm_adapter_instance is None:\n        _crm_adapter_instance = CRMAdapter(redis_url)\n        \n    return _crm_adapter_instance"