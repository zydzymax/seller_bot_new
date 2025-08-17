"""
handlers.py — Telegram Handlers для SoVAni AI-продавца (двойная LLM-обработка)
© SoVAni 2025
"""

import structlog
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram import Voice, Audio

logger = structlog.get_logger("ai_seller.handlers")

def setup_handlers(application, flow_manager, sanitizer, antiflood):
    # User message handler
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_message = update.message.text or ""
        
        # Антифлуд — если превышен лимит, молча игнорируем (или отправляем предупреждение)
        if await antiflood.is_limited(user_id):
            logger.info("antiflood_limit", user_id=user_id)
            await update.message.reply_text("⏳ Пожалуйста, не отправляйте сообщения так быстро.")
            return

        # Валидация/очистка ввода (XSS, prompt-injection)
        sanitized_message = sanitizer(user_message)
        if sanitized_message.startswith("❗️Извините") or sanitized_message.startswith("[удалено]"):
            await update.message.reply_text(sanitized_message)
            return

        logger.info("user_message_received", user_id=user_id, length=len(sanitized_message))
        
        # Главный диалоговый процессинг (OpenAI -> Claude)
        try:
            reply = await flow_manager.process(user_id, sanitized_message, context)
        except Exception as e:
            logger.error("flow_manager_error", error=str(e))
            reply = "⚠️ Произошла внутренняя ошибка, мы уже разбираемся."

        # Отправляем ответ с retry при ошибках
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await update.message.reply_text(reply, disable_web_page_preview=True)
                break
            except Exception as e:
                logger.warning("telegram_send_error", attempt=attempt+1, error=str(e))
                if attempt == max_retries - 1:
                    # Последняя попытка - отправляем короткое сообщение об ошибке
                    try:
                        await update.message.reply_text("⚠️ Произошла ошибка отправки. Повторите запрос.")
                    except:
                        pass
                else:
                    # Ждём перед повтором
                    import asyncio
                    await asyncio.sleep(2)

    # Voice message handler - ВРЕМЕННО ОТКЛЮЧЕНО
    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Антифлуд
        if await antiflood.is_limited(user_id):
            logger.info("antiflood_limit_voice", user_id=user_id)
            await update.message.reply_text("⏳ Пожалуйста, не отправляйте сообщения так быстро.")
            return

        logger.info("voice_message_disabled", user_id=user_id)
        await update.message.reply_text("🎙️ Голосовые сообщения временно отключены. Пожалуйста, напишите текстом.")

    # Audio message handler - ВРЕМЕННО ОТКЛЮЧЕНО
    async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Антифлуд  
        if await antiflood.is_limited(user_id):
            logger.info("antiflood_limit_audio", user_id=user_id)
            await update.message.reply_text("⏳ Пожалуйста, не отправляйте сообщения так быстро.")
            return

        logger.info("audio_message_disabled", user_id=user_id)
        await update.message.reply_text("🎵 Аудио сообщения временно отключены. Пожалуйста, напишите текстом.")

    # Photo message handler
    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Антифлуд
        if await antiflood.is_limited(user_id):
            logger.info("antiflood_limit_photo", user_id=user_id)
            await update.message.reply_text("⏳ Пожалуйста, не отправляйте сообщения так быстро.")
            return

        logger.info("photo_message_received", user_id=user_id, photo_count=len(update.message.photo))
        
        try:
            # Процессинг фото через flow_manager
            reply = await flow_manager.process_photo(user_id, update.message.photo, context)
        except Exception as e:
            logger.error("photo_flow_manager_error", error=str(e))
            reply = "⚠️ Произошла ошибка при обработке фото."

        # Отправляем ответ с retry при ошибках
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await update.message.reply_text(reply, disable_web_page_preview=True)
                break
            except Exception as e:
                logger.warning("telegram_send_error", attempt=attempt+1, error=str(e))
                if attempt == max_retries - 1:
                    try:
                        await update.message.reply_text("⚠️ Произошла ошибка отправки. Повторите запрос.")
                    except:
                        pass
                else:
                    import asyncio
                    await asyncio.sleep(2)

    # Регистрируем хендлеры
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(
        MessageHandler(filters.VOICE, handle_voice)
    )
    application.add_handler(
        MessageHandler(filters.AUDIO, handle_audio)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )
