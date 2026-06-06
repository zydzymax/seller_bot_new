"""
Message Router - Telegram handler wrapper for flow router

Обертка для использования flow_router в Telegram handlers (polling mode)
"""

from telegram import Update
from telegram.ext import ContextTypes

from utils.logging import get_logger
from dialog.flow_router import get_flow_manager

logger = get_logger(__name__)


async def route_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram handler для обычных сообщений

    Args:
        update: Telegram update
        context: Bot context
    """
    if not update.message or not update.message.text:
        return

    try:
        # Get flow router
        flow_manager = await get_flow_manager()

        # Prepare data in webhook format
        user_info = {
            "user_id": update.effective_user.id,
            "username": update.effective_user.username,
            "first_name": update.effective_user.first_name,
            "last_name": update.effective_user.last_name,
            "chat_id": update.effective_chat.id,
            "chat_type": update.effective_chat.type,
            "is_bot": update.effective_user.is_bot,
        }

        message_data = {
            "message_id": update.message.message_id,
            "text": update.message.text,
            "from": {
                "id": update.effective_user.id,
                "username": update.effective_user.username,
                "first_name": update.effective_user.first_name,
            },
            "chat": {
                "id": update.effective_chat.id,
                "type": update.effective_chat.type,
            },
        }

        update_dict = {"update_id": update.update_id, "message": message_data}

        # Process via flow router
        result = await flow_manager.process_message(
            user_info=user_info,
            message_text=update.message.text,
            message_data=message_data,
            update=update_dict,
        )

        # Response is already sent by flow manager
        logger.info(f"Message processed: {result.get('status')}")

    except Exception as e:
        logger.error(f"Error routing message: {e}", exc_info=True)
        await update.message.reply_text(
            "Извините, произошла ошибка. Попробуйте еще раз."
        )
