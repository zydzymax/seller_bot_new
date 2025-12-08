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
            'greeted_today': self.greeted_today
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
            greeted_today=data.get('greeted_today', False)
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

        # LLM orchestrator
        self.llm = get_orchestrator()

        # Conversation logger
        self.conv_db = get_conversation_db()

        # Learner
        self.learner = get_learner()

        # Telegram bot
        token = os.getenv('TELEGRAM_TOKEN')
        self.bot = Bot(token=token) if token else None

        logger.info(f"UniversalFlowManager initialized for domain: {self.domain_name}")

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

            # Extract entities from message
            extracted_slots = await self._extract_slots(message_text, context)

            # Update context slots
            for slot_name, slot_value in extracted_slots.items():
                if slot_value is not None:
                    context.slots[slot_name] = slot_value
                    logger.info(f"Extracted slot: {slot_name} = {slot_value}")

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

        # Use LLM to extract
        extraction_prompt = self._build_extraction_prompt(message_text, slots_to_extract)

        try:
            llm_response = await self.llm.generate_response(
                user_prompt=extraction_prompt,
                context={'chat_id': context.chat_id}
            )

            # Parse response (expecting JSON)
            extracted = self._parse_extraction_response(llm_response.content)
            return extracted

        except Exception as e:
            logger.error(f"Error extracting slots: {e}")
            return {}

    def _build_extraction_prompt(self, message: str, slots: List[Dict]) -> str:
        """Создать промпт для extraction"""
        prompt = f"""Extract data from user message.

Message: "{message}"

Extract the following fields (return JSON):
"""
        for slot in slots:
            prompt += f"\n- {slot['name']}: {slot.get('description', '')}"
            if 'examples' in slot:
                prompt += f" (examples: {', '.join(slot['examples'][:3])})"

        prompt += "\n\nReturn JSON with extracted values or null if not found."
        prompt += "\nExample: {\"business_niche\": \"real_estate\", \"current_lead_volume\": 50}"

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

                break

    def _evaluate_condition(self, condition: str, context: UniversalDialogContext) -> bool:
        """
        Простая оценка условия

        Примеры:
        - "all_qualify_slots_filled and qualified"
        - "client_interested_in_pricing"
        """
        if not condition:
            return True

        # Check for specific patterns
        if "all_" in condition and "_slots_filled" in condition:
            # Extract stage name
            import re
            match = re.search(r'all_(\w+)_slots_filled', condition)
            if match:
                stage = match.group(1).upper()
                return self._are_stage_slots_filled(stage, context)

        if "client_responded" in condition:
            return context.message_count > 1

        # Default: assume true
        return True

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

        # Build context for LLM
        llm_context = {
            'current_state': context.current_state,
            'collected_slots': context.slots,
            'missing_slots': self._get_missing_slots(context),
            'message_count': context.message_count,
            'domain': context.domain
        }

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
            llm_response = await self.llm.generate_response(
                user_prompt=message_text,
                context=llm_context
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
