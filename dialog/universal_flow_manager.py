"""
Universal Flow Manager - Работает с любым доменом через конфигурацию

Загружает domain config и использует его для управления диалогом
"""

import os
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum

import redis.asyncio as redis
from telegram import Bot
from telegram.error import TelegramError

from utils.logging import get_logger
from utils.domain_loader import get_current_domain_config, get_current_domain_name
from utils.conversation_logger import (
    get_conversation_db,
    ConversationLog,
    LeadAttributes,
    ConversationOutcome
)
from utils.simple_learner import get_learner
from llm.orchestrator import get_orchestrator
from dialog.intent_helpers import (
    is_agreement,
    is_decision_maker_self,
    is_rejection,
    quick_slot_extraction,
    get_intent,
    extract_number
)

logger = get_logger(__name__)


@dataclass
class UniversalDialogContext:
    """Универсальный контекст диалога"""
    conversation_id: str
    chat_id: int
    user_id: int
    domain: str

    # Current state
    current_state: str = "GREETING"

    # Dynamic slots (собранные данные)
    slots: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    started_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)
    message_count: int = 0

    # Flags
    greeted_today: bool = False

    # История сообщений (последние N сообщений)
    message_history: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for Redis storage"""
        return {
            'conversation_id': self.conversation_id,
            'chat_id': self.chat_id,
            'user_id': self.user_id,
            'domain': self.domain,
            'current_state': self.current_state,
            'slots': self.slots,
            'started_at': self.started_at,
            'last_message_at': self.last_message_at,
            'message_count': self.message_count,
            'greeted_today': self.greeted_today,
            'message_history': self.message_history[-10:]  # Храним последние 10 сообщений
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'UniversalDialogContext':
        """Create from dict (Redis load)"""
        return cls(
            conversation_id=data['conversation_id'],
            chat_id=data['chat_id'],
            user_id=data['user_id'],
            domain=data.get('domain', 'unknown'),
            current_state=data.get('current_state', 'GREETING'),
            slots=data.get('slots', {}),
            started_at=data.get('started_at', time.time()),
            last_message_at=data.get('last_message_at', time.time()),
            message_count=data.get('message_count', 0),
            greeted_today=data.get('greeted_today', False),
            message_history=data.get('message_history', [])
        )


class UniversalFlowManager:
    """
    Универсальный Flow Manager

    Работает с любым доменом через domain_loader
    """

    def __init__(self):
        self.domain_config = get_current_domain_config()
        self.domain_name = get_current_domain_name()

        # Redis для хранения контекста
        redis_url = os.getenv('REDIS_ADDR', 'redis://localhost:6379/0')
        self.redis_client = redis.from_url(redis_url, decode_responses=False)

        # LLM orchestrator (initialized lazily)
        self._llm = None

        # Conversation logger
        self.conv_db = get_conversation_db()

        # Learner
        self.learner = get_learner()

        # Telegram bot
        token = os.getenv('TELEGRAM_TOKEN')
        self.bot = Bot(token=token) if token else None

        logger.info(f"UniversalFlowManager initialized for domain: {self.domain_name}")

    async def get_llm(self):
        """Lazy initialization of LLM orchestrator"""
        if self._llm is None:
            self._llm = await get_orchestrator()
        return self._llm

    async def get_context(self, chat_id: int) -> UniversalDialogContext:
        """Получить или создать контекст диалога"""
        key = f"universal_dialog:context:{chat_id}"

        try:
            cached_data = await self.redis_client.get(key)
            if cached_data:
                data = json.loads(cached_data.decode('utf-8'))
                return UniversalDialogContext.from_dict(data)
        except Exception as e:
            logger.error(f"Error loading context from Redis: {e}")

        # Create new context
        context = UniversalDialogContext(
            conversation_id=f"conv_{chat_id}_{int(time.time())}",
            chat_id=chat_id,
            user_id=chat_id,  # Для Telegram обычно совпадает
            domain=self.domain_name,
            current_state="GREETING"
        )

        return context

    async def save_context(self, context: UniversalDialogContext):
        """Сохранить контекст в Redis"""
        key = f"universal_dialog:context:{context.chat_id}"
        ttl = int(os.getenv('SESSION_TTL_SEC', '3600'))

        try:
            data_json = json.dumps(context.to_dict(), default=str)
            await self.redis_client.setex(key, ttl, data_json)
        except Exception as e:
            logger.error(f"Error saving context to Redis: {e}")

    async def process_message(
        self,
        user_info: Dict[str, Any],
        message_text: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать входящее сообщение (совместимость с old flow_manager)

        Args:
            user_info: Информация о пользователе
            message_text: Текст сообщения
            message_data: Данные сообщения
            update: Полный Telegram update

        Returns:
            Dict с результатом обработки
        """
        chat_id = user_info.get('chat_id')
        update_id = update.get('update_id')

        try:
            # Load context
            context = await self.get_context(chat_id)
            context.user_id = user_info.get('user_id', chat_id)
            context.message_count += 1
            context.last_message_at = time.time()

            # Log message
            self.conv_db.save_message(
                conversation_id=context.conversation_id,
                role="user",
                content=message_text
            )

            # Добавляем сообщение в историю контекста
            context.message_history.append({"role": "user", "content": message_text})

            # Получаем последнее сообщение бота для контекста
            last_bot_message = ""
            for msg in reversed(context.message_history[:-1]):  # Исключаем только что добавленное
                if msg.get("role") == "assistant":
                    last_bot_message = msg.get("content", "")
                    break

            # ===== БЫСТРАЯ ЭКСТРАКЦИЯ (без LLM) =====
            # Сначала пробуем извлечь данные паттернами
            user_intent = get_intent(message_text)
            logger.info(f"User intent: {user_intent}")

            quick_slots = quick_slot_extraction(message_text, last_bot_message)
            if quick_slots:
                logger.info(f"Quick extraction found slots: {quick_slots}")
                for slot_name, slot_value in quick_slots.items():
                    if slot_value is not None:
                        context.slots[slot_name] = slot_value
                        logger.info(f"Quick saved slot: {slot_name} = {slot_value}")

            # Проверка: если пользователь сказал "я" и это ответ на вопрос о ЛПР
            if is_decision_maker_self(message_text):
                context.slots["decision_authority"] = "owner"
                logger.info("Set decision_authority = owner via pattern match")

            # ===== LLM ЭКСТРАКЦИЯ (для сложных случаев) =====
            logger.info(f"Extracting slots from message: '{message_text[:50]}...', state: {context.current_state}")
            extracted_slots = await self._extract_slots(message_text, context)
            logger.info(f"LLM extracted slots result: {extracted_slots}")

            # Update context slots (не перезаписываем уже заполненные)
            for slot_name, slot_value in extracted_slots.items():
                if slot_value is not None and slot_value != "null" and slot_name not in context.slots:
                    context.slots[slot_name] = slot_value
                    logger.info(f"LLM saved slot: {slot_name} = {slot_value}")

            logger.info(f"Current slots after extraction: {context.slots}")

            # ===== ОБРАБОТКА СОГЛАСИЯ ("да", "давай", "ок") =====
            if user_intent == "agreement":
                logger.info("User expressed agreement - triggering state transition")
                # Помечаем что пользователь согласился на следующий шаг
                context.slots["_user_agreed"] = True

            # Determine next state (if needed)
            await self._update_state(context)

            # Generate response
            response = await self._generate_response(context, message_text)

            # Log bot response
            self.conv_db.save_message(
                conversation_id=context.conversation_id,
                role="assistant",
                content=response
            )

            # Добавляем ответ бота в историю
            context.message_history.append({"role": "assistant", "content": response})

            # Send response via Telegram
            await self._send_telegram_message(chat_id, response, update_id)

            # Save context
            await self.save_context(context)

            # Update conversation in DB
            self._update_conversation_log(context)

            return {
                'status': 'ok',
                'state': context.current_state,
                'response_sent': True,
                'conversation_id': context.conversation_id
            }

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

            # Send error message
            error_msg = "Извините, произошла ошибка. Попробуйте еще раз."
            try:
                await self._send_telegram_message(chat_id, error_msg, update_id)
            except:
                pass

            return {
                'status': 'error',
                'error': str(e),
                'response_sent': False
            }

    async def _send_telegram_message(self, chat_id: int, text: str, update_id: Optional[int] = None):
        """Отправить сообщение через Telegram Bot API"""
        if not self.bot:
            logger.warning("Telegram bot not initialized, skipping message send")
            return

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None
            )
            logger.info(f"Message sent to {chat_id}", update_id=update_id)
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")

    async def _extract_slots(self, message_text: str, context: UniversalDialogContext) -> Dict[str, Any]:
        """
        Извлечь slots из сообщения

        Использует LLM для extraction
        """
        # Get slots for current state
        current_state_config = self._get_state_config(context.current_state)
        if not current_state_config:
            return {}

        collect_slots = current_state_config.get('collect_slots', [])
        if not collect_slots:
            return {}

        # Prepare extraction prompt
        slots_to_extract = []
        for slot_name in collect_slots:
            slot_config = self._get_slot_config(slot_name)
            if slot_config:
                slots_to_extract.append(slot_config)

        if not slots_to_extract:
            return {}

        # Получаем последнее сообщение бота для контекста
        last_bot_message = ""
        for msg in reversed(context.message_history):
            if msg.get("role") == "assistant":
                last_bot_message = msg.get("content", "")[:200]  # Берем первые 200 символов
                break

        # Use LLM to extract
        extraction_prompt = self._build_extraction_prompt(message_text, slots_to_extract, last_bot_message)

        try:
            llm = await self.get_llm()
            llm_response = await llm.generate_response(
                user_prompt=extraction_prompt,
                context={'chat_id': context.chat_id}
            )

            # Parse response (expecting JSON)
            extracted = self._parse_extraction_response(llm_response.content)
            return extracted

        except Exception as e:
            logger.error(f"Error extracting slots: {e}")
            return {}

    def _build_extraction_prompt(self, message: str, slots: List[Dict], last_bot_message: str = "") -> str:
        """Создать промпт для extraction с контекстом"""
        prompt = f"""Извлеки данные из ответа пользователя.

"""
        if last_bot_message:
            prompt += f"""Предыдущий вопрос бота: "{last_bot_message}"
"""
        prompt += f"""Ответ пользователя: "{message}"

Извлеки следующие поля (верни JSON):
"""
        for slot in slots:
            prompt += f"\n- {slot['name']}: {slot.get('description', '')}"
            if 'examples' in slot:
                prompt += f" (примеры значений: {', '.join(slot['examples'][:3])})"

        prompt += """

ВАЖНО:
- Если пользователь отвечает "я", "сам", "лично я" на вопрос "кто принимает решения" → decision_authority: "owner"
- Если число (например "300", "1000") → это current_lead_volume
- Верни JSON с извлеченными значениями или null если не найдено

Пример: {"business_niche": "стоматология", "current_lead_volume": 300, "decision_authority": "owner"}"""

        return prompt

    def _parse_extraction_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM extraction response"""
        try:
            # Try to find JSON in response
            import re
            json_match = re.search(r'\{[^}]+\}', response)
            if json_match:
                return json.loads(json_match.group(0))
        except:
            pass

        return {}

    async def _update_state(self, context: UniversalDialogContext):
        """
        Обновить state на основе собранных slots
        """
        current_state_config = self._get_state_config(context.current_state)
        if not current_state_config:
            return

        # Check transitions
        transitions = current_state_config.get('transitions', [])

        for transition in transitions:
            to_state = transition.get('to')
            condition = transition.get('condition', '')

            # Simple condition evaluation
            if self._evaluate_condition(condition, context):
                # Transition to new state
                old_state = context.current_state
                context.current_state = to_state

                logger.info(f"State transition: {old_state} → {to_state}")

                # Log transition
                self.conv_db.save_state_transition(
                    conversation_id=context.conversation_id,
                    from_state=old_state,
                    to_state=to_state,
                    trigger=condition
                )

                # Сбрасываем флаг согласия после перехода
                if '_user_agreed' in context.slots:
                    del context.slots['_user_agreed']

                break

    def _evaluate_condition(self, condition: str, context: UniversalDialogContext) -> bool:
        """
        Простая оценка условия

        Примеры:
        - "all_qualify_slots_filled and qualified"
        - "client_interested_in_pricing"
        """
        if not condition:
            return False  # Без условия - не переходить

        import re

        # Check for specific patterns
        if "all_" in condition and "_slots_filled" in condition:
            # Extract stage name
            match = re.search(r'all_(\w+)_slots_filled', condition)
            if match:
                stage = match.group(1).upper()
                return self._are_stage_slots_filled(stage, context)

        if "client_responded" in condition:
            return context.message_count > 0  # Любой ответ клиента

        if "showed_interest" in condition:
            return context.message_count > 0  # Считаем интерес если отвечает

        if "qualified" in condition:
            # Проверяем заполнены ли QUALIFY слоты
            return self._are_stage_slots_filled('QUALIFY', context)

        if "is_decision_maker" in condition:
            authority = context.slots.get('decision_authority')
            return authority in ['owner', 'decision_maker']

        if "client_interested_in_pricing" in condition:
            # Переход в OFFER когда DIAGNOSE слоты заполнены ИЛИ пользователь согласился
            diagnose_filled = self._are_stage_slots_filled('DIAGNOSE', context)
            user_agreed = context.slots.get('_user_agreed', False)
            return diagnose_filled or user_agreed

        if "budget_matches" in condition:
            return 'budget_range' in context.slots

        if "no_objections" in condition:
            return True  # Пока без системы отслеживания возражений

        if "all_close_slots_filled" in condition:
            return self._are_stage_slots_filled('CLOSE', context)

        # Проверка на согласие пользователя (для любых условий)
        if "user_agreed" in condition or "agreement" in condition:
            user_agreed = context.slots.get('_user_agreed', False)

            # Дополнительная проверка на "has_some_X_data"
            if "has_some_qualify_data" in condition:
                has_data = 'business_niche' in context.slots or 'current_lead_volume' in context.slots
                return user_agreed and has_data

            if "has_some_diagnose_data" in condition:
                has_data = 'decision_authority' in context.slots or 'pain_point' in context.slots
                return user_agreed and has_data

            return user_agreed

        # Default: НЕ переходить если условие неизвестно
        return False

    def _are_stage_slots_filled(self, stage: str, context: UniversalDialogContext) -> bool:
        """Проверить заполнены ли все slots этапа"""
        collection_order = self.domain_config.slots.get('collection_order', {})
        required_slots = collection_order.get(stage, [])

        for slot_name in required_slots:
            if slot_name not in context.slots or context.slots[slot_name] is None:
                return False

        return True

    async def _generate_response(self, context: UniversalDialogContext, message_text: str) -> str:
        """
        Генерировать ответ бота

        Использует:
        1. System prompt из domain config
        2. Learner для выбора вариантов (если есть)
        3. LLM для generation
        """
        # Get system prompt
        system_prompt = self.domain_config.system_prompt

        # Build context for LLM - более явно показываем что уже собрано
        missing_slots = self._get_missing_slots(context)

        # Фильтруем внутренние слоты
        visible_slots = {k: v for k, v in context.slots.items() if not k.startswith('_')}

        llm_context = {
            'current_state': context.current_state,
            'collected_slots': visible_slots,
            'missing_slots': missing_slots,
            'message_count': context.message_count,
            'domain': context.domain
        }

        # Добавляем явное напоминание в system_prompt о собранных данных
        if visible_slots:
            slots_summary = "\n\n⚠️ УЖЕ СОБРАННАЯ ИНФОРМАЦИЯ (НЕ СПРАШИВАЙ ПОВТОРНО):\n"
            for slot_name, slot_value in visible_slots.items():
                slots_summary += f"- {slot_name}: {slot_value}\n"
            system_prompt = system_prompt + slots_summary

        if missing_slots:
            system_prompt += f"\n\n📋 ЕЩЁ НУЖНО УЗНАТЬ: {', '.join(missing_slots)}\n"

        # ЖЁСТКОЕ ОГРАНИЧЕНИЕ на формат ответа
        system_prompt += """

🚫 СТРОГО ЗАПРЕЩЕНО В ЭТОМ ОТВЕТЕ:
- ТОЛЬКО ОДИН ВОПРОС или ОДИН ЗАПРОС за сообщение!
- Плохо: "Какая боль? И кто решает?" - ДВА ВОПРОСА!
- Плохо: "Уточните email и название компании" - ДВА ЗАПРОСА!
- Хорошо: "Какой у вас email?" - ОДИН ЗАПРОС
- НЕ задавай вопросы из списка УЖЕ СОБРАННОЙ ИНФОРМАЦИИ выше
- НЕ называй цену пока не спросил бюджет клиента!
- НЕ переходи к сбору контактов пока не обсудил бюджет!
"""

        # Проверка бюджета перед ценой
        if context.current_state == "OFFER" and "budget_range" not in context.slots:
            system_prompt += """
⚠️ ТЫ В СОСТОЯНИИ OFFER, НО БЮДЖЕТ НЕ ВЫЯСНЕН!
- СНАЧАЛА спроси: "Какой бюджет комфортен для старта?"
- ТОЛЬКО ПОСЛЕ ответа про бюджет называй цены
- НЕ рекомендуй пакеты пока не знаешь бюджет!
"""

        # Обработка несоответствия бюджета
        if "budget_range" in context.slots:
            budget = context.slots.get("budget_range")
            system_prompt += f"""
💰 БЮДЖЕТ КЛИЕНТА: {budget}
- Подбери пакет ПОД БЮДЖЕТ клиента
- Если бюджет маленький (до 50к) - предложи Базовый или рассрочку
- НЕ игнорируй бюджет, НЕ говори "Отлично!" если бюджет не совпадает с рекомендацией
"""

        if context.message_count > 1:
            system_prompt += """
- НЕ говори "Привет", "Здравствуйте" - ты УЖЕ поздоровался!
- НЕ представляйся заново - клиент знает кто ты
- Сразу переходи к делу
"""

        # Check if learner has variants for current context
        learner_context = f"{context.current_state}:{context.domain}"
        variant = self.learner.select_variant(learner_context)

        if variant:
            variant_id, variant_content = variant
            # Use variant as additional instruction
            system_prompt += f"\n\n[RECOMMENDED APPROACH]: {variant_content}"

            # Store variant_id in context for later feedback
            context.slots['_last_variant_id'] = variant_id
            context.slots['_last_variant_context'] = learner_context

        # Generate response
        try:
            llm = await self.get_llm()
            llm_response = await llm.generate_response(
                user_prompt=message_text,
                context=llm_context,
                system_prompt=system_prompt,  # Передаем промпт домена
                message_history=context.message_history[:-1]  # Передаем историю (без текущего сообщения)
            )

            return llm_response.content

        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "Извините, произошла ошибка. Можете повторить?"

    def _get_missing_slots(self, context: UniversalDialogContext) -> List[str]:
        """Получить список незаполненных slots для текущего state"""
        current_state_config = self._get_state_config(context.current_state)
        if not current_state_config:
            return []

        collect_slots = current_state_config.get('collect_slots', [])
        missing = []

        for slot_name in collect_slots:
            if slot_name not in context.slots or context.slots[slot_name] is None:
                missing.append(slot_name)

        return missing

    def _get_state_config(self, state_name: str) -> Optional[Dict]:
        """Получить конфигурацию state"""
        states = self.domain_config.states.get('states', [])
        for state in states:
            if state.get('name') == state_name:
                return state
        return None

    def _get_slot_config(self, slot_name: str) -> Optional[Dict]:
        """Получить конфигурацию slot"""
        slots = self.domain_config.slots.get('slots', [])
        for slot in slots:
            if slot.get('name') == slot_name:
                return slot
        return None

    def _update_conversation_log(self, context: UniversalDialogContext):
        """Обновить лог диалога в БД"""
        # Update conversation
        conv_data = {
            'conversation_id': context.conversation_id,
            'chat_id': context.chat_id,
            'user_id': context.user_id,
            'domain': context.domain,
            'started_at': context.started_at,
            'last_message_at': context.last_message_at,
            'message_count': context.message_count,
            'current_state': context.current_state,
        }

        # Create ConversationLog object
        conv_log = ConversationLog(
            conversation_id=context.conversation_id,
            chat_id=context.chat_id,
            user_id=context.user_id,
            domain=context.domain,
            started_at=context.started_at,
            last_message_at=context.last_message_at,
            message_count=context.message_count,
            current_state=context.current_state,
            collected_slots=context.slots,
            lead_attrs=self._build_lead_attributes(context)
        )

        self.conv_db.save_conversation(conv_log)

    def _build_lead_attributes(self, context: UniversalDialogContext) -> LeadAttributes:
        """Построить lead attributes из slots"""
        attrs = LeadAttributes()

        # Map slots to lead attributes
        slot_mapping = {
            'business_niche': 'business_niche',
            'current_lead_volume': 'lead_volume',
            'lead_channels': 'lead_channels',
            'pain_point': 'pain_point',
            'current_solution': 'current_solution',
            'decision_authority': 'decision_authority',
            'budget_range': 'budget_range',
        }

        for slot_name, attr_name in slot_mapping.items():
            if slot_name in context.slots:
                setattr(attrs, attr_name, context.slots[slot_name])

        attrs.reached_stage = context.current_state

        return attrs


# Global instance
_universal_flow_manager = None


def get_universal_flow_manager() -> UniversalFlowManager:
    """Get global universal flow manager instance"""
    global _universal_flow_manager
    if _universal_flow_manager is None:
        _universal_flow_manager = UniversalFlowManager()
    return _universal_flow_manager


async def process_universal_message(chat_id: int, message_text: str) -> str:
    """
    Convenience function to process message

    Args:
        chat_id: Telegram chat ID
        message_text: User message

    Returns:
        Bot response
    """
    manager = get_universal_flow_manager()
    return await manager.process_message(chat_id, message_text)
