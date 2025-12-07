"""
Admin Commands Handler
Обработка админ-команд для управления ботом и обучения
"""

import os
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes

from utils.conversation_logger import (
    get_conversation_db,
    ConversationOutcome
)
from utils.simple_learner import get_learner
from utils.domain_loader import (
    get_current_domain_name,
    get_domain_loader,
    switch_domain
)


# Admin user IDs (from env)
ADMIN_CHAT_IDS = [
    int(x.strip())
    for x in os.getenv('ADMIN_CHAT_IDS', '').split(',')
    if x.strip()
]


def is_admin(chat_id: int) -> bool:
    """Проверка является ли пользователь админом"""
    return chat_id in ADMIN_CHAT_IDS


async def cmd_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /result - фиксация результата диалога

    Использование:
    /result <conversation_id> success [reason]
    /result <conversation_id> fail <reason>

    Примеры:
    /result conv_12345 success
    /result conv_12345 fail price_objection
    /result conv_12345 fail no_budget Клиент сказал дорого
    """
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Неправильный формат\n\n"
            "Использование:\n"
            "/result <conv_id> success [reason]\n"
            "/result <conv_id> fail <reason>\n\n"
            "Примеры:\n"
            "/result conv_12345 success\n"
            "/result conv_12345 fail price_objection\n"
            "/result conv_12345 fail no_budget Клиент сказал дорого"
        )
        return

    conversation_id = context.args[0]
    outcome = context.args[1].lower()

    if outcome not in ['success', 'fail']:
        await update.message.reply_text("❌ Outcome должен быть 'success' или 'fail'")
        return

    # Reason (опционально)
    reason = None
    notes = None
    if len(context.args) > 2:
        reason = context.args[2]

    if len(context.args) > 3:
        notes = ' '.join(context.args[3:])

    # Сохранить feedback
    db = get_conversation_db()

    try:
        db.save_outcome_feedback(
            conversation_id=conversation_id,
            outcome=outcome,
            reason=reason,
            feedback_by=f"admin_{update.effective_user.id}",
            notes=notes
        )

        await update.message.reply_text(
            f"✅ Результат сохранен!\n\n"
            f"Conversation: {conversation_id}\n"
            f"Outcome: {outcome}\n"
            f"Reason: {reason or 'не указана'}\n"
            f"Notes: {notes or 'нет'}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /stats - статистика по диалогам

    Использование:
    /stats [domain]
    """
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам")
        return

    domain = context.args[0] if context.args else None

    db = get_conversation_db()
    stats = db.get_conversation_stats(domain=domain)

    if not stats:
        await update.message.reply_text("📊 Нет данных")
        return

    # Форматируем статистику
    text = "📊 **Статистика диалогов**\n\n"

    if domain:
        text += f"Domain: {domain}\n\n"

    total = sum(s['count'] for s in stats.values())

    text += f"Всего диалогов: {total}\n\n"

    for outcome, data in sorted(stats.items(), key=lambda x: x[1]['count'], reverse=True):
        count = data['count']
        avg_msg = data['avg_messages']
        percentage = (count / total * 100) if total > 0 else 0

        text += f"{outcome}:\n"
        text += f"  • Количество: {count} ({percentage:.1f}%)\n"
        text += f"  • Средняя длина: {avg_msg:.1f} сообщений\n\n"

    await update.message.reply_text(text, parse_mode='Markdown')


async def cmd_learning_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /learning_stats - статистика системы обучения

    Использование:
    /learning_stats [context]
    """
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам")
        return

    ctx = context.args[0] if context.args else None

    learner = get_learner()
    stats = learner.get_stats(context=ctx)

    if not stats:
        await update.message.reply_text("📊 Нет данных об обучении")
        return

    # Форматируем
    text = "🧠 **Статистика обучения**\n\n"

    for context_name, variants in stats.items():
        text += f"**{context_name}**\n\n"

        for v in variants:
            text += f"ID: {v['variant_id']}\n"
            text += f"Content: {v['content']}\n"
            text += f"Trials: {v['trials']}, Successes: {v['successes']}\n"
            text += f"Success Rate: {v['success_rate']:.1%}\n"
            text += f"Confidence: {v['confidence']:.2f}\n\n"

    # Telegram message limit - split if needed
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, parse_mode='Markdown')


async def cmd_switch_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /switch_domain - переключить режим работы бота

    Использование:
    /switch_domain <domain_name>
    """
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам")
        return

    if not context.args:
        # Показать текущий домен и доступные
        current = get_current_domain_name()
        loader = get_domain_loader()
        available = loader.list_available_domains()

        text = f"**Текущий режим:** {current}\n\n"
        text += "**Доступные режимы:**\n"
        for d in available:
            text += f"  • {d}\n"

        text += "\nДля переключения:\n"
        text += "/switch_domain <domain_name>"

        await update.message.reply_text(text, parse_mode='Markdown')
        return

    domain_name = context.args[0]

    try:
        new_config = switch_domain(domain_name)

        await update.message.reply_text(
            f"✅ Режим переключен!\n\n"
            f"Новый режим: **{new_config.domain}**\n"
            f"Версия: {new_config.version}\n"
            f"Описание: {new_config.description}",
            parse_mode='Markdown'
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def cmd_domain_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /domain - информация о текущем режиме"""
    from utils.domain_loader import get_current_domain_config

    config = get_current_domain_config()

    text = f"**Текущий режим бота**\n\n"
    text += f"Domain: {config.domain}\n"
    text += f"Version: {config.version}\n"
    text += f"Language: {config.language}\n"
    text += f"Active: {config.active}\n\n"
    text += f"Description:\n{config.description}\n\n"
    text += f"Slots defined: {len(config.slots.get('slots', []))}\n"
    text += f"States defined: {len(config.states.get('states', []))}\n"

    await update.message.reply_text(text, parse_mode='Markdown')


async def cmd_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin_help - справка по админ-командам"""
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("⛔ Эта команда доступна только администраторам")
        return

    text = """
**Админ-команды AI Seller Bot**

*Управление результатами:*
/result <conv_id> success [reason]
/result <conv_id> fail <reason> [notes]

*Статистика:*
/stats [domain] - статистика диалогов
/learning_stats [context] - статистика обучения

*Режимы работы:*
/domain - текущий режим
/switch_domain <name> - переключить режим
  • textile_manufacturing - швейная фабрика
  • ai_seller_self - продажа AI-ботов

*Примеры:*
/result conv_12345 success
/result conv_67890 fail price_objection Клиент сказал дорого
/stats ai_seller_self
/switch_domain ai_seller_self
"""

    await update.message.reply_text(text, parse_mode='Markdown')


# Export handlers
ADMIN_COMMAND_HANDLERS = {
    'result': cmd_result,
    'stats': cmd_stats,
    'learning_stats': cmd_learning_stats,
    'switch_domain': cmd_switch_domain,
    'domain': cmd_domain_info,
    'admin_help': cmd_admin_help
}
