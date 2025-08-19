"""
flow_manager.py — Flow Manager для SoVAni AI-продавца с жёсткими бизнес-правилами.

- FSM на Redis с валидацией MOQ/сроков/цен  
- Интеграция с LLM оркестратором и CRM
- Жёсткие guard'ы для бизнес-логики
- Контекст диалога и состояние пользователя
- Telegram bot API для отправки ответов

© SoVAni 2025
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
import yaml

import redis.asyncio as redis
from telegram import Bot
from telegram.error import TelegramError

from utils.logging import get_logger
from llm.orchestrator import get_orchestrator
from adapters.crm_adapter import get_crm_adapter, ContactData, OrderDetails

logger = get_logger(__name__)


class FlowState(Enum):
    """Состояния FSM для воронки продаж"""
    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"  
    QUANTITY_COLORS = "quantity_colors"
    MOQ_VALIDATION = "moq_validation"
    FABRIC_DETAILS = "fabric_details"
    PACKAGING_LOGISTICS = "packaging_logistics"
    PRICING_MODE = "pricing_mode"
    CONTACT_COLLECTION = "contact_collection"
    FINALIZATION = "finalization"
    COMPLETED = "completed"


@dataclass
class DialogContext:
    """Контекст диалога пользователя"""
    user_id: int
    chat_id: int
    current_state: FlowState = FlowState.GREETING
    
    # Данные заказа
    product_type: Optional[str] = None
    total_quantity: Optional[int] = None
    colors_count: Optional[int] = None
    quantity_per_color: Optional[int] = None
    
    fabric_composition: Optional[str] = None
    fabric_density: Optional[str] = None
    hardware_fittings: Optional[str] = None
    
    packaging_requirements: Optional[str] = None
    labeling_requirements: Optional[str] = None
    logistics_requirements: Optional[str] = None
    
    pricing_mode: str = "factory_quote"  # factory_quote | client_budget
    client_budget: Optional[float] = None
    
    # Контактные данные
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    company_name: Optional[str] = None
    
    # Метаданные
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
            
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь"""
        result = asdict(self)
        result['current_state'] = self.current_state.value
        return result
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DialogContext':
        """Десериализация из словаря"""
        if 'current_state' in data:
            data['current_state'] = FlowState(data['current_state'])
        return cls(**data)


class BusinessRulesValidator:
    """Валидатор бизнес-правил"""
    
    def __init__(self, rules: Dict[str, Any]):
        self.rules = rules
        
    def validate_moq(self, total_qty: int, colors_count: int) -> Dict[str, Any]:
        """Валидация минимального заказа (MOQ)"""
        moq_per_color = self.rules.get('moq', {}).get('turnkey_per_color', 1000)
        
        if not total_qty or not colors_count:
            return {
                "valid": False,
                "error": "missing_data",
                "message": "Не указано количество или количество цветов"
            }
            
        quantity_per_color = total_qty // colors_count
        
        if quantity_per_color < moq_per_color:
            return {
                "valid": False,
                "error": "moq_violation",
                "message": f"При {colors_count} цветах получается {quantity_per_color} шт/цвет, но минимум {moq_per_color} шт/цвет",
                "suggestions": [
                    f"Уменьшить количество цветов до {total_qty // moq_per_color}",
                    f"Увеличить общий объём до {colors_count * moq_per_color} штук",
                    "Разделить заказ на несколько этапов"
                ]
            }
            
        return {"valid": True, "quantity_per_color": quantity_per_color}


class FlowManager:
    """Менеджер потока диалога с FSM"""
    
    def __init__(self, redis_url: str = None):
        self.redis_client = redis.from_url(redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0'))
        self.telegram_bot = None
        if os.getenv('TELEGRAM_TOKEN'):
            self.telegram_bot = Bot(token=os.getenv('TELEGRAM_TOKEN'))
        self.llm_orchestrator = None
        self.crm_adapter = None
        self.business_rules = {}
        self.rules_validator = None
        self._load_business_rules()
        
    def _load_business_rules(self):
        """Загрузка бизнес-правил"""
        rules_path = Path(__file__).parent.parent / "config" / "business_rules.yaml"
        try:
            with open(rules_path, 'r', encoding='utf-8') as f:
                self.business_rules = yaml.safe_load(f)
                self.rules_validator = BusinessRulesValidator(self.business_rules)
        except Exception as e:
            logger.error(f"Failed to load business rules: {e}")
            self.business_rules = {}
            self.rules_validator = BusinessRulesValidator({})
            
    async def initialize(self):
        """Инициализация компонентов"""
        self.llm_orchestrator = await get_orchestrator()
        self.crm_adapter = await get_crm_adapter()
        logger.info("Flow manager initialized")
        
    async def get_context(self, chat_id: int) -> DialogContext:
        """Получение контекста диалога из Redis"""
        key = f"dialog:context:{chat_id}"
        try:
            cached_data = await self.redis_client.get(key)
            if cached_data:
                data = json.loads(cached_data.decode('utf-8'))
                return DialogContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load context: {e}")
        import time
        return DialogContext(user_id=0, chat_id=chat_id, created_at=time.time(), updated_at=time.time())
        
    async def save_context(self, context: DialogContext):
        """Сохранение контекста в Redis"""
        key = f"dialog:context:{context.chat_id}"
        try:
            import time
            context.updated_at = time.time()
            context.message_count += 1
            await self.redis_client.setex(
                key,
                int(os.getenv('SESSION_TTL_SEC', '3600')),
                json.dumps(context.to_dict(), default=str)
            )
        except Exception as e:
            logger.error(f"Failed to save context: {e}")
            
    async def send_telegram_message(self, chat_id: int, text: str) -> bool:
        """Отправка сообщения в Telegram"""
        if not self.telegram_bot:
            logger.warning("Telegram bot not initialized")
            return False
        try:
            await self.telegram_bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
            return True
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
            
    async def transition_to_state(self, context: DialogContext, new_state: FlowState, reason: str = None):
        """Переход FSM в новое состояние"""
        old_state = context.current_state
        context.current_state = new_state
        
        logger.info(f"FSM transition: {old_state.value} -> {new_state.value}", 
                   chat_id=context.chat_id, reason=reason)
                   
    def extract_order_data(self, message_text: str, context: DialogContext) -> Dict[str, Any]:
        """Извлечение данных заказа из сообщения"""
        extracted = {}
        text_lower = message_text.lower()
        
        # Извлечение чисел
        import re
        numbers = [int(x) for x in re.findall(r'\b\d+\b', message_text)]
        
        # Определение типа продукта
        product_keywords = {
            'толстовка': 'толстовка',
            'худи': 'худи', 
            'футболка': 'футболка',
            'пижама': 'пижама',
            'костюм': 'костюм',
            'свитшот': 'свитшот',
            'майка': 'майка',
            'шорты': 'шорты'
        }
        
        for keyword, product_type in product_keywords.items():
            if keyword in text_lower:
                extracted['product_type'] = product_type
                break
                
        # Извлечение количества и цветов
        if numbers:
            if len(numbers) >= 2 and any(word in text_lower for word in ['цвет', 'расцветка', 'вариант']):
                extracted['total_quantity'] = numbers[0]
                extracted['colors_count'] = numbers[1]
            elif len(numbers) == 1:
                extracted['total_quantity'] = numbers[0]
                
        # Извлечение контактов
        if any(word in text_lower for word in ['телефон', '+7', '8-9', '89', 'номер']):
            phone_match = re.search(r'(?:\+7|8)[\s\-]?\(?(\d{3})\)?[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})', message_text)
            if phone_match:
                extracted['contact_phone'] = phone_match.group(0)
                
        # Извлечение имени
        name_patterns = [
            r'меня зовут\s+([а-яё]+)',
            r'я\s+([а-яё]+)',
            r'имя\s+([а-яё]+)',
        ]
        for pattern in name_patterns:
            match = re.search(pattern, text_lower)
            if match:
                extracted['contact_name'] = match.group(1).capitalize()
                break
                
        return extracted
        
    async def process_fsm_logic(self, context: DialogContext, message_text: str) -> Dict[str, Any]:
        """Логика FSM переходов"""
        current_state = context.current_state
        extracted_data = self.extract_order_data(message_text, context)
        
        # Обновляем контекст данными из сообщения
        for key, value in extracted_data.items():
            if hasattr(context, key) and value:
                setattr(context, key, value)
                
        result = {"state_changed": False, "validation_errors": []}
        
        # FSM переходы
        if current_state == FlowState.GREETING:
            if extracted_data.get('product_type'):
                await self.transition_to_state(context, FlowState.PRODUCT_INQUIRY, "product_mentioned")
                result["state_changed"] = True
            elif any(word in message_text.lower() for word in ['привет', 'добро', 'здравств']):
                # Остаемся в greeting
                pass
            else:
                await self.transition_to_state(context, FlowState.PRODUCT_INQUIRY, "general_inquiry")
                result["state_changed"] = True
                
        elif current_state == FlowState.PRODUCT_INQUIRY:
            if extracted_data.get('product_type'):
                context.product_type = extracted_data['product_type']
                await self.transition_to_state(context, FlowState.QUANTITY_COLORS, "product_specified")
                result["state_changed"] = True
                
        elif current_state == FlowState.QUANTITY_COLORS:
            if extracted_data.get('total_quantity') and extracted_data.get('colors_count'):
                context.total_quantity = extracted_data['total_quantity']
                context.colors_count = extracted_data['colors_count']
                
                # MOQ валидация
                moq_result = self.rules_validator.validate_moq(
                    context.total_quantity, context.colors_count
                )
                
                if moq_result["valid"]:
                    context.quantity_per_color = moq_result["quantity_per_color"]
                    await self.transition_to_state(context, FlowState.FABRIC_DETAILS, "moq_valid")
                    result["state_changed"] = True
                else:
                    await self.transition_to_state(context, FlowState.MOQ_VALIDATION, "moq_violation")
                    result["validation_errors"] = [moq_result]
                    result["state_changed"] = True
                    
        elif current_state == FlowState.MOQ_VALIDATION:
            # Обработка изменений после нарушения MOQ
            if extracted_data.get('total_quantity') or extracted_data.get('colors_count'):
                total = extracted_data.get('total_quantity', context.total_quantity)
                colors = extracted_data.get('colors_count', context.colors_count)
                
                if total and colors:
                    moq_result = self.rules_validator.validate_moq(total, colors)
                    if moq_result["valid"]:
                        context.total_quantity = total
                        context.colors_count = colors
                        context.quantity_per_color = moq_result["quantity_per_color"]
                        await self.transition_to_state(context, FlowState.FABRIC_DETAILS, "moq_corrected")
                        result["state_changed"] = True
                    else:
                        result["validation_errors"] = [moq_result]
                        
        elif current_state == FlowState.FABRIC_DETAILS:
            fabric_mentioned = any(word in message_text.lower() for word in 
                                 ['хлопок', 'полиэстер', '100%', '%', 'состав', 'ткань', 'футер'])
            if fabric_mentioned:
                context.fabric_composition = message_text
                await self.transition_to_state(context, FlowState.PACKAGING_LOGISTICS, "fabric_specified")
                result["state_changed"] = True
                
        elif current_state == FlowState.PACKAGING_LOGISTICS:
            packaging_mentioned = any(word in message_text.lower() for word in 
                                    ['упаковка', 'пакет', 'коробка', 'этикетка', 'бирка'])
            if packaging_mentioned or 'нет' in message_text.lower():
                context.packaging_requirements = message_text
                await self.transition_to_state(context, FlowState.PRICING_MODE, "packaging_specified")
                result["state_changed"] = True
                
        elif current_state == FlowState.PRICING_MODE:
            if any(word in message_text.lower() for word in ['бюджет', 'рублей', 'тысяч', 'стоит']):
                context.pricing_mode = "client_budget"
                budget_numbers = [int(x) for x in re.findall(r'\b\d+\b', message_text)]
                if budget_numbers:
                    context.client_budget = max(budget_numbers)  # Берем максимальное число как бюджет
                await self.transition_to_state(context, FlowState.CONTACT_COLLECTION, "budget_specified")
                result["state_changed"] = True
            elif any(word in message_text.lower() for word in ['расчет', 'рассчитайте', 'цена']):
                context.pricing_mode = "factory_quote"
                await self.transition_to_state(context, FlowState.CONTACT_COLLECTION, "quote_requested")
                result["state_changed"] = True
                
        elif current_state == FlowState.CONTACT_COLLECTION:
            if extracted_data.get('contact_phone'):
                context.contact_phone = extracted_data['contact_phone']
                if extracted_data.get('contact_name'):
                    context.contact_name = extracted_data['contact_name']
                await self.transition_to_state(context, FlowState.FINALIZATION, "contacts_provided")
                result["state_changed"] = True
            elif extracted_data.get('contact_name') and not context.contact_phone:
                context.contact_name = extracted_data['contact_name']
                # Остаемся в состоянии, ждем телефон
                
        elif current_state == FlowState.FINALIZATION:
            # Финализация - переход в completed или возврат к редактированию
            if any(word in message_text.lower() for word in ['да', 'верно', 'правильно', 'согласен']):
                await self.transition_to_state(context, FlowState.COMPLETED, "confirmed")
                result["state_changed"] = True
            elif any(word in message_text.lower() for word in ['изменить', 'поправить', 'другой']):
                # Определяем что изменить и возвращаемся в соответствующее состояние
                if any(word in message_text.lower() for word in ['количество', 'цвет']):
                    await self.transition_to_state(context, FlowState.QUANTITY_COLORS, "correction_requested")
                elif any(word in message_text.lower() for word in ['ткань', 'состав']):
                    await self.transition_to_state(context, FlowState.FABRIC_DETAILS, "correction_requested")
                else:
                    await self.transition_to_state(context, FlowState.CONTACT_COLLECTION, "correction_requested")
                result["state_changed"] = True
                
        return result
        
    async def process_completed_order(self, context: DialogContext) -> Dict[str, Any]:
        """Обработка завершенного заказа - отправка в CRM"""
        try:
            # Подготовка контактных данных
            contact_data = ContactData(
                name=context.contact_name,
                phone=context.contact_phone,
                email=context.contact_email,
                company=context.company_name
            )
            
            # Подготовка данных заказа
            order_details = OrderDetails(
                product_type=context.product_type,
                total_quantity=context.total_quantity,
                colors_count=context.colors_count,
                fabric_composition=context.fabric_composition,
                pricing_mode=context.pricing_mode,
                estimated_value=context.client_budget
            )
            
            # Внешний ID для идемпотентности
            external_id = f"tg_{context.chat_id}_{context.user_id}_{int(context.created_at)}"
            
            # Отправка в CRM
            crm_result = await self.crm_adapter.create_or_update_lead(
                external_id=external_id,
                contact_data=contact_data,
                order_details=order_details,
                source="telegram_bot"
            )
            
            logger.info("Order sent to CRM", 
                       chat_id=context.chat_id,
                       external_id=external_id,
                       crm_success=crm_result.get('success', False),
                       contact_id=crm_result.get('contact_id'),
                       lead_id=crm_result.get('lead_id'))
            
            return {
                "crm_success": crm_result.get('success', False),
                "crm_result": crm_result,
                "external_id": external_id
            }
            
        except Exception as e:
            logger.error(f"Failed to process completed order: {e}", 
                        chat_id=context.chat_id, 
                        exc_info=True)
            return {
                "crm_success": False,
                "error": str(e)
            }
    
    def get_order_summary(self, context: DialogContext) -> str:
        """Генерация сводки заказа для финализации"""
        summary_lines = ["📋 **Сводка вашего заказа:**", ""]
        
        if context.product_type:
            summary_lines.append(f"🔸 **Продукт:** {context.product_type}")
            
        if context.total_quantity and context.colors_count:
            quantity_per_color = context.total_quantity // context.colors_count
            summary_lines.append(f"🔸 **Количество:** {context.total_quantity} шт ({context.colors_count} цвета по {quantity_per_color} шт)")
            
        if context.fabric_composition:
            summary_lines.append(f"🔸 **Ткань:** {context.fabric_composition}")
            
        if context.packaging_requirements:
            summary_lines.append(f"🔸 **Упаковка:** {context.packaging_requirements}")
            
        if context.pricing_mode == "client_budget" and context.client_budget:
            summary_lines.append(f"🔸 **Бюджет:** {context.client_budget:,.0f} руб.")
        elif context.pricing_mode == "factory_quote":
            summary_lines.append(f"🔸 **Ценообразование:** Расчет от завода")
            
        if context.contact_name:
            summary_lines.append(f"🔸 **Контакт:** {context.contact_name}")
            
        if context.contact_phone:
            summary_lines.append(f"🔸 **Телефон:** {context.contact_phone}")
            
        summary_lines.extend(["", "Все данные верны?"])
        
        return "\n".join(summary_lines)

    async def process_message(
        self,
        user_info: Dict[str, Any],
        message_text: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Основная обработка сообщения через FSM"""
        chat_id = user_info['chat_id']
        
        try:
            context = await self.get_context(chat_id)
            context.user_id = user_info.get('user_id', 0)
            
            # FSM обработка
            fsm_result = await self.process_fsm_logic(context, message_text)
            
            # Специальная обработка состояний
            if context.current_state == FlowState.FINALIZATION and not fsm_result.get("validation_errors"):
                # Отправляем сводку заказа
                summary_text = self.get_order_summary(context)
                await self.send_telegram_message(chat_id, summary_text)
                await self.save_context(context)
                
                return {
                    "state": context.current_state.value,
                    "response_sent": True,
                    "order_summary_sent": True,
                    "fsm_processed": True
                }
                
            elif context.current_state == FlowState.COMPLETED:
                # Обработка завершенного заказа
                crm_result = await self.process_completed_order(context)
                
                if crm_result.get("crm_success"):
                    completion_text = f"""✅ **Отлично! Заказ принят.**

📝 Ваша заявка передана менеджеру.
🆔 ID заявки: `{crm_result.get('external_id', 'N/A')}`

📞 **Что дальше:**
• Менеджер свяжется с вами в течение 2-3 часов
• Подготовит коммерческое предложение
• Обсудит детали и сроки производства

Спасибо за обращение в SoVAni! 🙏"""
                else:
                    completion_text = f"""⚠️ **Заказ принят, но возникли технические сложности.**

Ваши данные сохранены:
• Продукт: {context.product_type or 'не указан'}
• Количество: {context.total_quantity or 'не указано'}
• Контакт: {context.contact_phone or 'не указан'}

Менеджер свяжется с вами в ближайшее время."""

                await self.send_telegram_message(chat_id, completion_text)
                await self.save_context(context)
                
                return {
                    "state": context.current_state.value,
                    "response_sent": True,
                    "order_completed": True,
                    "crm_success": crm_result.get("crm_success", False),
                    "fsm_processed": True
                }
            
            # Если есть ошибки валидации (например, MOQ), обрабатываем их
            if fsm_result.get("validation_errors"):
                for error in fsm_result["validation_errors"]:
                    if error.get("error") == "moq_violation":
                        response_text = f"""❌ {error['message']}

📋 **Предлагаю варианты:**
• {error['suggestions'][0]}
• {error['suggestions'][1]}
• {error['suggestions'][2]}

Как будем корректировать заказ?"""
                        
                        await self.send_telegram_message(chat_id, response_text)
                        await self.save_context(context)
                        
                        return {
                            "state": context.current_state.value,
                            "response_sent": True,
                            "validation_error": True,
                            "fsm_processed": True
                        }
            
            # Генерация ответа через LLM с учетом FSM состояния
            llm_context = {
                "state": context.current_state.value,
                "dialog_context": context.to_dict(),
                "business_rules": self.business_rules,
                "fsm_result": fsm_result
            }
            
            llm_response = await self.llm_orchestrator.generate_response(
                message_text,
                context=llm_context
            )
            
            # Отправка ответа
            response_sent = await self.send_telegram_message(chat_id, llm_response.content)
            
            # Сохранение контекста
            await self.save_context(context)
            
            return {
                "state": context.current_state.value,
                "response_sent": response_sent,
                "llm_used": llm_response.model_used,
                "fsm_processed": True,
                "state_changed": fsm_result.get("state_changed", False)
            }
            
        except Exception as e:
            logger.error(f"Flow processing error: {e}")
            fallback_text = "Извините, произошла техническая ошибка. Пожалуйста, повторите ваш запрос."
            await self.send_telegram_message(chat_id, fallback_text)
            return {"status": "error", "error": str(e)}


# Singleton
_flow_manager_instance = None

async def get_flow_manager(redis_url: str = None) -> FlowManager:
    global _flow_manager_instance
    if _flow_manager_instance is None:
        _flow_manager_instance = FlowManager(redis_url)
        await _flow_manager_instance.initialize()
    return _flow_manager_instance