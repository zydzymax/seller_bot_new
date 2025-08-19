"""
crm_adapter.py  CRM 040?B5@ 4;O SoVAni AI-?@>402F0 (AmoCRM stub).

- Async HTTP :;85=B A @5B@0O<8 8 B09<0CB0<8
- 45<?>B5=B=>ABL ?> external_id (chat_id:session_id)
- DLQ 4;O =5CA?5H=KE 70?@>A>2 2 Redis
- =B53@0F8O A DialogContext 40==K<8
- Prometheus <5B@8:8 8 health checks

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
    """!B0BCAK ;84>2 2 CRM"""
    NEW = "new"
    QUALIFIED = "qualified"  
    PROPOSAL_SENT = "proposal_sent"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


@dataclass
class ContactData:
    """>=B0:B=K5 40==K5 :;85=B0"""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    
    
@dataclass  
class OrderDetails:
    """5B0;8 70:070"""
    product_type: Optional[str] = None
    total_quantity: Optional[int] = None
    colors_count: Optional[int] = None
    fabric_composition: Optional[str] = None
    pricing_mode: Optional[str] = None
    estimated_value: Optional[float] = None


class CRMAdapter:
    """40?B5@ 4;O 8=B53@0F88 A CRM A8AB5<0<8"""
    
    def __init__(self, redis_url: str = None):
        # 0AB@>9:8 CRM (AmoCRM 2 40==>< A;CG05)
        self.crm_domain = os.getenv('AMOCRM_DOMAIN', 'your-domain.amocrm.ru')
        self.api_token = os.getenv('AMOCRM_API_TOKEN', '')
        self.user_hash = os.getenv('AMOCRM_USER_HASH', '')
        
        # Redis 4;O DLQ 8 :MH8@>20=8O
        self.redis_client = redis.from_url(redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0'))
        
        # HTTP :;85=B A =0AB@>9:0<8
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0),
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'SoVAni-AI-Seller/1.0'
            }
        )
        
        # 0AB@>9:8 @5B@052
        self.max_retries = int(os.getenv('CRM_MAX_RETRIES', '3'))
        self.retry_delay = float(os.getenv('CRM_RETRY_DELAY', '1.0'))
        
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None,
        retry_count: int = 0
    ) -> Dict[str, Any]:
        """K?>;=5=85 HTTP 70?@>A0 : CRM API A @5B@0O<8"""
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
        """>102;5=85 =5CA?5H=>3> 70?@>A0 2 DLQ"""
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
            # 3@0=8G8205< @07<5@ DLQ
            await self.redis_client.ltrim("crm:dlq", 0, 999)
            logger.info(f"Added failed CRM operation to DLQ: {operation}")
        except Exception as e:
            logger.error(f"Failed to add to DLQ: {e}")
            
    async def find_contact_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """>8A: :>=B0:B0 ?> =><5@C B5;5D>=0"""
        if not phone:
            return None
            
        try:
            # Stub implementation - 2>72@0I05< 703;CH:C
            logger.info(f"Searching contact by phone: {phone[:3]}***{phone[-2:]}")
            
            #  @50;L=>9 @50;870F88 745AL 1C45B 2K7>2 AmoCRM API
            # response = await self._make_request('GET', 'contacts', {'query': phone})
            
            # >:0 2>72@0I05< None (:>=B0:B =5 =0945=)
            return None
            
        except Exception as e:
            logger.error(f"Failed to search contact: {e}")
            await self._add_to_dlq("find_contact", {"phone": phone}, str(e))
            return None
            
    async def create_contact(self, contact_data: ContactData) -> Optional[int]:
        """!>740=85 =>2>3> :>=B0:B0"""
        try:
            crm_contact = []
            
            # $>@<8@>20=85 40==KE :>=B0:B0 4;O AmoCRM
            contact_fields = {}
            
            if contact_data.name:
                contact_fields["name"] = contact_data.name
                
            # Custom fields (=C6=> 7=0BL ID ?>;59 2 AmoCRM)
            custom_fields = []
            
            if contact_data.phone:
                custom_fields.append({
                    "field_id": 123456,  # ID ?>;O B5;5D>=0 2 AmoCRM
                    "values": [{"value": contact_data.phone, "enum_code": "WORK"}]
                })
                
            if contact_data.email:
                custom_fields.append({
                    "field_id": 123457,  # ID ?>;O email 2 AmoCRM  
                    "values": [{"value": contact_data.email, "enum_code": "WORK"}]
                })
                
            if contact_data.company:
                contact_fields["company_name"] = contact_data.company
                
            if custom_fields:
                contact_fields["custom_fields_values"] = custom_fields
                
            crm_contact.append(contact_fields)
            
            # Stub implementation - ;>38@C5< GB> A>740;8 1K :>=B0:B
            logger.info(f"Would create CRM contact", extra={
                "contact_name": contact_data.name,
                "has_phone": bool(contact_data.phone),
                "has_email": bool(contact_data.email),
                "company": contact_data.company
            })
            
            #  @50;L=>9 @50;870F88:
            # response = await self._make_request('POST', 'contacts', crm_contact)
            # return response['_embedded']['contacts'][0]['id']
            
            # >72@0I05< mock ID
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
        """!>740=85 ;840 (A45;:8)"""
        try:
            # $>@<8@>20=85 40==KE ;840
            lead_data = []
            
            lead_fields = {
                "name": f"0:07 >B AI-1>B0: {order_details.product_type or '"5:AB8;L'}",
                "status_id": 123,  # ID AB0BCA0 "5@28G=K9 :>=B0:B"
                "pipeline_id": 456,  # ID 2>@>=:8
                "contacts_id": [contact_id]
            }
            
            # #AB0=>2:0 AB>8<>AB8 5A;8 5ABL
            if order_details.estimated_value:
                lead_fields["price"] = int(order_details.estimated_value)
                
            # Custom fields 4;O 45B0;59 70:070
            custom_fields = []
            
            if order_details.total_quantity:
                custom_fields.append({
                    "field_id": 789012,  # ID ?>;O ">;8G5AB2>"
                    "values": [{"value": str(order_details.total_quantity)}]
                })
                
            if order_details.colors_count:
                custom_fields.append({
                    "field_id": 789013,  # ID ?>;O ">;8G5AB2> F25B>2"
                    "values": [{"value": str(order_details.colors_count)}]
                })
                
            if order_details.fabric_composition:
                custom_fields.append({
                    "field_id": 789014,  # ID ?>;O "!>AB02 B:0=8"
                    "values": [{"value": order_details.fabric_composition}]
                })
                
            if order_details.pricing_mode:
                custom_fields.append({
                    "field_id": 789015,  # ID ?>;O " 568< F5=>>1@07>20=8O"
                    "values": [{"value": order_details.pricing_mode}]
                })
                
            # =5H=89 ID 4;O 845<?>B5=B=>AB8
            custom_fields.append({
                "field_id": 789016,  # ID ?>;O "External ID"
                "values": [{"value": external_id}]
            })
            
            if custom_fields:
                lead_fields["custom_fields_values"] = custom_fields
                
            lead_data.append(lead_fields)
            
            # Stub implementation
            logger.info(f"Would create CRM lead", extra={
                "external_id": external_id,
                "contact_id": contact_id,
                "product_type": order_details.product_type,
                "total_quantity": order_details.total_quantity,
                "estimated_value": order_details.estimated_value,
                "source": source
            })
            
            #  @50;L=>9 @50;870F88:
            # response = await self._make_request('POST', 'leads', lead_data)
            # return response['_embedded']['leads'][0]['id']
            
            # >72@0I05< mock ID
            return 67890
            
        except Exception as e:
            logger.error(f"Failed to create lead: {e}")
            await self._add_to_dlq("create_lead", {
                "contact_id": contact_id,
                "order_details": order_details.__dict__,
                "external_id": external_id,
                "source": source
            }, str(e))
            return None
            
    async def update_lead(
        self, 
        lead_id: int, 
        updates: Dict[str, Any]
    ) -> bool:
        """1=>2;5=85 ACI5AB2CNI53> ;840"""
        try:
            # Stub implementation
            logger.info(f"Would update CRM lead {lead_id}", extra={
                "lead_id": lead_id,
                "updates": updates
            })
            
            #  @50;L=>9 @50;870F88:
            # response = await self._make_request('PATCH', f'leads/{lead_id}', updates)
            # return True
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update lead {lead_id}: {e}")
            await self._add_to_dlq("update_lead", {
                "lead_id": lead_id,
                "updates": updates
            }, str(e))
            return False
            
    async def create_or_update_lead(
        self,
        external_id: str,
        contact_data: ContactData,
        order_details: OrderDetails,
        source: str = "telegram_bot"
    ) -> Dict[str, Any]:
        """
        !>740=85 8;8 >1=>2;5=85 ;840 A ?>;=K< pipeline
        
        Args:
            external_id: =5H=89 845=B8D8:0B>@ (=0?@8<5@, tg_chat_id)
            contact_data: >=B0:B=K5 40==K5
            order_details: 5B0;8 70:070
            source: AB>G=8: ;840
            
        Returns:
             57C;LB0B >?5@0F88 A ID A>740==KE/>1=>2;5==KE 70?8A59
        """
        result = {
            "success": False,
            "contact_id": None,
            "lead_id": None,
            "operation": "unknown",
            "external_id": external_id
        }
        
        try:
            # @>25@:0 845<?>B5=B=>AB8 G5@57 :MH 2 Redis
            cache_key = f"crm:processed:{external_id}"
            cached_result = await self.redis_client.get(cache_key)
            
            if cached_result:
                logger.info(f"Returning cached CRM result for {external_id}")
                return json.loads(cached_result.decode('utf-8'))
                
            # 1. >8A: ACI5AB2CNI53> :>=B0:B0 ?> B5;5D>=C
            contact_id = None
            if contact_data.phone:
                existing_contact = await self.find_contact_by_phone(contact_data.phone)
                if existing_contact:
                    contact_id = existing_contact.get('id')
                    result["operation"] = "found_existing_contact"
                    
            # 2. !>740=85 :>=B0:B0 5A;8 =5 =0945=
            if not contact_id:
                contact_id = await self.create_contact(contact_data)
                if contact_id:
                    result["operation"] = "created_contact"
                else:
                    logger.error("Failed to create contact")
                    return result
                    
            result["contact_id"] = contact_id
            
            # 3. !>740=85 ;840
            lead_id = await self.create_lead(
                contact_id=contact_id,
                order_details=order_details,
                external_id=external_id,
                source=source
            )
            
            if lead_id:
                result["lead_id"] = lead_id
                result["success"] = True
                result["operation"] += "_and_created_lead"
                
                # MH8@>20=85 @57C;LB0B0 =0 1 G0A
                await self.redis_client.setex(
                    cache_key,
                    3600,
                    json.dumps(result, default=str)
                )
                
                logger.info(f"Successfully processed CRM lead", extra={
                    "external_id": external_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "operation": result["operation"]
                })
            else:
                logger.error("Failed to create lead")
                
        except Exception as e:
            logger.error(f"CRM processing failed for {external_id}: {e}")
            await self._add_to_dlq("create_or_update_lead", {
                "external_id": external_id,
                "contact_data": contact_data.__dict__,
                "order_details": order_details.__dict__,
                "source": source
            }, str(e))
            
        return result
        
    async def process_dlq_items(self, batch_size: int = 10) -> Dict[str, Any]:
        """1@01>B:0 M;5<5=B>2 87 DLQ"""
        processed = 0
        successful = 0
        failed = 0
        
        try:
            # >;CG5=85 M;5<5=B>2 87 DLQ
            items = await self.redis_client.lrange("crm:dlq", 0, batch_size - 1)
            
            for item_data in items:
                try:
                    item = json.loads(item_data.decode('utf-8'))
                    
                    # @>?CA:05< M;5<5=BK :>B>@K5 A;8H:>< AB0@K5 (> 24 G0A>2)
                    if time.time() - item.get('timestamp', 0) > 86400:
                        await self.redis_client.lrem("crm:dlq", 1, item_data)
                        continue
                        
                    # @>?CA:05< M;5<5=BK A 1>;LH8< :>;8G5AB2>< ?>?KB>:
                    if item.get('retry_count', 0) >= 5:
                        await self.redis_client.lrem("crm:dlq", 1, item_data)
                        failed += 1
                        continue
                        
                    # >?KB:0 ?>2B>@=>9 >1@01>B:8
                    operation = item.get('operation')
                    data = item.get('data', {})
                    
                    success = False
                    
                    if operation == "create_or_update_lead":
                        result = await self.create_or_update_lead(
                            external_id=data.get('external_id'),
                            contact_data=ContactData(**data.get('contact_data', {})),
                            order_details=OrderDetails(**data.get('order_details', {})),
                            source=data.get('source', 'dlq_retry')
                        )
                        success = result.get('success', False)
                        
                    if success:
                        await self.redis_client.lrem("crm:dlq", 1, item_data)
                        successful += 1
                        logger.info(f"Successfully processed DLQ item: {operation}")
                    else:
                        # #25;8G8205< AGQBG8: ?>?KB>:
                        item['retry_count'] = item.get('retry_count', 0) + 1
                        await self.redis_client.lset("crm:dlq", processed, json.dumps(item))
                        failed += 1
                        
                    processed += 1
                    
                except Exception as e:
                    logger.error(f"Error processing DLQ item: {e}")
                    failed += 1
                    processed += 1
                    
        except Exception as e:
            logger.error(f"DLQ processing error: {e}")
            
        return {
            "processed": processed,
            "successful": successful,
            "failed": failed
        }
        
    async def health_check(self) -> Dict[str, Any]:
        """@>25@:0 A>AB>O=8O CRM 040?B5@0"""
        health = {
            "status": "healthy",
            "crm_configured": bool(self.api_token),
            "redis_connected": False,
            "dlq_size": 0
        }
        
        try:
            # @>25@:0 Redis
            await self.redis_client.ping()
            health["redis_connected"] = True
            
            #  07<5@ DLQ
            dlq_size = await self.redis_client.llen("crm:dlq")
            health["dlq_size"] = dlq_size
            
            if dlq_size > 100:
                health["status"] = "degraded"
                health["warning"] = f"Large DLQ size: {dlq_size}"
                
        except Exception as e:
            health["status"] = "unhealthy"
            health["error"] = str(e)
            
        return health
        
    async def close(self):
        """0:@KB85 A>548=5=89"""
        try:
            await self.http_client.aclose()
            await self.redis_client.close()
        except Exception as e:
            logger.error(f"Error closing CRM adapter: {e}")


# Singleton instance
_crm_adapter_instance = None


async def get_crm_adapter(redis_url: str = None) -> CRMAdapter:
    """>;CG5=85 singleton M:75<?;O@0 CRM 040?B5@0"""
    global _crm_adapter_instance
    
    if _crm_adapter_instance is None:
        _crm_adapter_instance = CRMAdapter(redis_url)
        
    return _crm_adapter_instance