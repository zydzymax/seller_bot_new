import os
from telegram.helpers import send_message
from utils.logging import get_logger

logger = get_logger(__name__)
MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "")  # ID чата менеджера


async def send_summary(chat_id: int, context, update_id: int = None):
    """Отправляет сводку пользователю с кнопкой связи с менеджером"""

    # Формируем сводку
    summary = f"""📋 **Ваш заказ:**
• Изделие: {context.product_type or 'не указано'}
• Цвета: {context.colors_count or 'не указано'}
• Количество: {context.total_quantity or 'не указано'} шт
• Схема: {'под ключ' if context.work_scheme == 'turnkey' else 'давальческое сырьё' if context.work_scheme else 'не указано'}
• Телефон: {context.contact_phone or 'не указан'}"""

    # Inline клавиатура
    keyboard = [["Изменить количество", "Изменить цвета"], ["Связать с менеджером"]]

    try:
        await send_message(chat_id, summary, keyboard, inline=True)
        return True
    except Exception as e:
        logger.error(f"Failed to send summary: {e}")
        return False


async def handoff_to_manager(chat_id: int, context, username: str = None):
    """Передает заказчика менеджеру"""

    # Уведомление пользователю
    user_msg = "✅ Передал ваш контакт менеджеру. Ответит в течение часа."
    try:
        await send_message(chat_id, user_msg)
    except Exception:
        pass

    # Уведомление менеджеру
    if MANAGER_CHAT_ID:
        manager_msg = f"""🔔 **Новый заказ:**
• От: @{username or 'unknown'} (ID: {chat_id})
• Изделие: {context.product_type or '?'}
• Количество: {context.total_quantity or '?'} шт × {context.colors_count or '?'} цв.
• Схема: {'под ключ' if context.work_scheme == 'turnkey' else 'давальч.' if context.work_scheme else '?'}
• Телефон: {context.contact_phone or 'не указан'}
• Время: {context.created_at or 'сейчас'}"""

        manager_keyboard = None
        if context.contact_phone:
            # Кнопка для звонка
            phone_clean = (
                context.contact_phone.replace("+", "").replace(" ", "").replace("-", "")
            )
            manager_keyboard = [
                [{"text": "📞 Позвонить", "url": f"tel:+{phone_clean}"}]
            ]

        try:
            await send_message(
                MANAGER_CHAT_ID, manager_msg, manager_keyboard, inline=True
            )
        except Exception as e:
            logger.error(f"Failed to notify manager: {e}")

    # Логируем handoff
    logger.info(
        "handoff",
        chat_id=chat_id,
        username=username,
        product=context.product_type,
        quantity=context.total_quantity,
        manager_notified=bool(MANAGER_CHAT_ID),
    )

    return True


def should_offer_handoff(context) -> bool:
    """Определяет, нужно ли предложить связь с менеджером"""
    # Если количество >= 3000 и схема работы определена
    if (
        context.total_quantity
        and context.total_quantity >= 3000
        and context.work_scheme
    ):
        return True

    # Если заполнены основные поля
    filled_key_fields = sum(
        [
            bool(context.product_type),
            bool(context.work_scheme),
            bool(context.total_quantity),
            bool(context.colors_count),
        ]
    )

    return filled_key_fields >= 3
