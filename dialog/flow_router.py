"""
Flow Router - Выбор flow manager на основе домена

Маршрутизирует обработку сообщений на правильный flow manager:
- textile_manufacturing → dialog/flow_manager.py (старый)
- ai_seller_self → dialog/universal_flow_manager.py (новый)
"""

import os
from typing import Dict, Any
from utils.logging import get_logger

logger = get_logger(__name__)

# Domain enum (local copy to avoid circular dependency)
class Domain:
    TEXTILE_MANUFACTURING = "textile_manufacturing"
    AI_SELLER_SELF = "ai_seller_self"


def get_current_domain_name() -> str:
    """Get current domain from environment or default"""
    return os.getenv('AI_SELLER_DOMAIN', Domain.TEXTILE_MANUFACTURING)


class FlowRouter:
    """
    Роутер flow managers

    Выбирает какой flow manager использовать в зависимости от домена
    """

    def __init__(self):
        self.domain = get_current_domain_name()
        self.flow_manager = None
        self._initialized = False

    async def _init_flow_manager(self):
        """Инициализировать правильный flow manager"""
        if self._initialized:
            return

        if self.domain == Domain.TEXTILE_MANUFACTURING:
            # Use old flow manager
            from dialog.flow_manager import get_flow_manager
            self.flow_manager = await get_flow_manager()
            logger.info("Using legacy FlowManager for textile_manufacturing")

        elif self.domain == Domain.AI_SELLER_SELF:
            # Use new two-layer flow manager (pre-bot + AI seller)
            from dialog.two_layer_flow_manager import get_two_layer_flow_manager
            self.flow_manager = get_two_layer_flow_manager()
            logger.info("Using TwoLayerFlowManager for ai_seller_self")

        else:
            logger.error(f"Unknown domain: {self.domain}")
            # Fallback to textile
            from dialog.flow_manager import get_flow_manager
            self.flow_manager = await get_flow_manager()
            logger.warning("Falling back to textile_manufacturing FlowManager")

        self._initialized = True

    async def process_message(
        self,
        user_info: Dict[str, Any],
        message_text: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать сообщение через правильный flow manager

        Args:
            user_info: Информация о пользователе
            message_text: Текст сообщения
            message_data: Данные сообщения
            update: Полный Telegram update

        Returns:
            Dict с результатом обработки
        """
        await self._init_flow_manager()

        return await self.flow_manager.process_message(
            user_info=user_info,
            message_text=message_text,
            message_data=message_data,
            update=update
        )

    async def process_callback_query(
        self,
        user_info: Dict[str, Any],
        callback_data: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать callback query (для inline кнопок)

        Args:
            user_info: Информация о пользователе
            callback_data: Данные callback
            message_data: Данные сообщения
            update: Полный Telegram update

        Returns:
            Dict с результатом обработки
        """
        await self._init_flow_manager()

        # Check if flow manager has callback query method
        if hasattr(self.flow_manager, 'process_callback_query'):
            return await self.flow_manager.process_callback_query(
                user_info=user_info,
                callback_data=callback_data,
                message_data=message_data,
                update=update
            )
        else:
            logger.warning(f"Flow manager {type(self.flow_manager).__name__} doesn't support callback queries")
            return {
                'status': 'unsupported',
                'message': 'Callback queries not supported in this mode'
            }

    async def reload(self):
        """Перезагрузить flow manager (после смены домена)"""
        self.domain = get_current_domain_name()
        self._initialized = False
        self.flow_manager = None
        await self._init_flow_manager()


# Global instance
_flow_router = None


async def get_flow_manager(redis_url: str = None) -> FlowRouter:
    """
    Get global flow router instance

    Совместимость с old flow_manager API

    Args:
        redis_url: Redis URL (игнорируется, используется из env)

    Returns:
        FlowRouter instance
    """
    global _flow_router
    if _flow_router is None:
        _flow_router = FlowRouter()
        await _flow_router._init_flow_manager()
    return _flow_router


async def reload_flow_manager():
    """Reload flow router (e.g., after domain switch)"""
    global _flow_router
    if _flow_router:
        await _flow_router.reload()
    else:
        _flow_router = await get_flow_manager()
    return _flow_router
