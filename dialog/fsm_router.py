"""
FSM Router — роутер состояний для AI-продавца.

ВАЖНО: Переходы между стадиями определяются КОДОМ, а не LLM!
"""

from typing import TYPE_CHECKING
from utils.logging import get_logger
from dialog.intent_helpers import (
    is_price_objection,
    is_timing_objection,
    is_competitor_objection,
    is_closing_signal,
    is_price_question,
    is_positive_confirmation,
    is_think_about_objection,
    is_soft_opt_out,
    is_contact_info,
    extract_contact,
)

if TYPE_CHECKING:
    from dialog.two_layer_flow_manager import TwoLayerContext

logger = get_logger(__name__)


# Конфигурация DIAG_Q вопросов (без LLM)
DIAG_QUESTIONS = [
    {
        "id": "confirm_pain",
        "template": "Правильно понимаю, что сейчас больше всего мешает именно {pain}?",
        "fallback": "Что сейчас больше всего мешает в обработке заявок?",
    },
    {
        "id": "pain_consequences",
        "template": "К чему это приводит: к потере клиентов, перегрузке команды или чему-то ещё?",
    },
]

# Максимум DIAG_Q вопросов
MAX_DIAG_QUESTIONS = 2


def decide_next_stage(
    context: "TwoLayerContext",
    user_message: str,
) -> str:
    """
    Определяет следующую FSM-стадию на основе:
    - текущей стадии
    - содержимого сообщения пользователя
    - флагов контекста

    ВАЖНО: Эта функция определяет переход, а не LLM!

    Returns:
        Название следующей стадии FSM
    """
    current_stage = context.stage
    logger.info(f"FSM: current_stage={current_stage}, message={user_message[:50]}...")

    # ===== ГЛОБАЛЬНЫЕ ПРОВЕРКИ (работают на любой стадии) =====

    # Контакт получен → CLOSED
    contact = extract_contact(user_message)
    if contact or is_contact_info(user_message):
        context.client_contact = contact or user_message.strip()
        context.contact_captured = True
        logger.info("FSM: Contact detected → CLOSED")
        return "CLOSED"

    # Мягкий отказ → NURTURE
    if is_soft_opt_out(user_message):
        context.soft_opt_out = True
        logger.info("FSM: Soft opt-out detected → NURTURE")
        return "NURTURE"

    # Вопрос о цене → PRICE_DISCUSSION (на любой стадии после PREBOT)
    if is_price_question(user_message) and current_stage not in ["PREBOT", "DIAG_Q"]:
        context.user_asked_price_recently = True
        logger.info("FSM: Price question detected → PRICE_DISCUSSION")
        return "PRICE_DISCUSSION"

    # Возражение по цене → OBJECTION_HANDLING
    if is_price_objection(user_message) and current_stage not in ["PREBOT", "DIAG_Q"]:
        context.objection_type = "price"
        logger.info("FSM: Price objection detected → OBJECTION_HANDLING")
        return "OBJECTION_HANDLING"

    # Возражение по времени → OBJECTION_HANDLING
    if is_timing_objection(user_message) and current_stage not in ["PREBOT", "DIAG_Q"]:
        context.objection_type = "timing"
        logger.info("FSM: Timing objection detected → OBJECTION_HANDLING")
        return "OBJECTION_HANDLING"

    # Конкурент → OBJECTION_HANDLING
    if is_competitor_objection(user_message) and current_stage not in [
        "PREBOT",
        "DIAG_Q",
    ]:
        context.objection_type = "competitor"
        logger.info("FSM: Competitor objection detected → OBJECTION_HANDLING")
        return "OBJECTION_HANDLING"

    # "Я подумаю" → OBJECTION_HANDLING
    if is_think_about_objection(user_message) and current_stage not in [
        "PREBOT",
        "DIAG_Q",
    ]:
        context.objection_type = "think_about"
        logger.info("FSM: Think about objection detected → OBJECTION_HANDLING")
        return "OBJECTION_HANDLING"

    # Явный сигнал закрытия → CLOSING
    if is_closing_signal(user_message) and current_stage not in ["PREBOT", "DIAG_Q"]:
        logger.info("FSM: Closing signal detected → CLOSING")
        return "CLOSING"

    # ===== СТАДИЙНЫЕ ПЕРЕХОДЫ =====

    if current_stage == "PREBOT":
        # PREBOT обрабатывается отдельно в process_message
        # Когда все слоты собраны, переход на DIAG_Q происходит там
        return "PREBOT"

    elif current_stage == "DIAG_Q":
        # Сохраняем ответ на текущий вопрос
        q_index = context.diag_question_index
        if q_index < len(DIAG_QUESTIONS):
            q_id = DIAG_QUESTIONS[q_index]["id"]
            context.diag_answers[q_id] = user_message

        # Переходим к следующему вопросу или к NEED_SUMMARY
        context.diag_question_index += 1

        if context.diag_question_index >= MAX_DIAG_QUESTIONS:
            context.problem_clarified = True
            logger.info("FSM: DIAG_Q complete → NEED_SUMMARY")
            return "NEED_SUMMARY"
        else:
            logger.info(f"FSM: DIAG_Q question {context.diag_question_index + 1}")
            return "DIAG_Q"

    elif current_stage == "NEED_SUMMARY":
        # После NEED_SUMMARY всегда идём в VALUE_PITCH
        context.has_need_summary = True
        logger.info("FSM: NEED_SUMMARY done → VALUE_PITCH")
        return "VALUE_PITCH"

    elif current_stage == "VALUE_PITCH":
        # После VALUE_PITCH проверяем реакцию
        if is_positive_confirmation(user_message):
            context.value_pitch_done = True
            logger.info("FSM: Positive confirmation → COMMIT_TEST")
            return "COMMIT_TEST"
        else:
            # Нейтральный ответ — тоже переходим в COMMIT_TEST
            context.value_pitch_done = True
            logger.info("FSM: VALUE_PITCH done → COMMIT_TEST")
            return "COMMIT_TEST"

    elif current_stage == "COMMIT_TEST":
        # После COMMIT_TEST проверяем интерес
        if is_positive_confirmation(user_message):
            context.commit_test_done = True
            logger.info("FSM: Interest confirmed → CLOSING")
            return "CLOSING"
        else:
            # Нейтральный ответ — переходим в CLOSING
            context.commit_test_done = True
            logger.info("FSM: COMMIT_TEST done → CLOSING")
            return "CLOSING"

    elif current_stage == "PRICE_DISCUSSION":
        # После обсуждения цены — CLOSING или OBJECTION_HANDLING
        context.price_discussed = True
        if is_price_objection(user_message):
            context.objection_type = "price"
            return "OBJECTION_HANDLING"
        else:
            logger.info("FSM: Price discussed → CLOSING")
            return "CLOSING"

    elif current_stage == "OBJECTION_HANDLING":
        # После обработки возражения — CLOSING или NURTURE
        if context.objection_type == "price":
            context.price_objection_count += 1
            if context.price_objection_count >= 3:
                logger.info("FSM: Too many price objections → NURTURE")
                return "NURTURE"

        # Проверяем реакцию
        if is_positive_confirmation(user_message) or is_closing_signal(user_message):
            logger.info("FSM: Objection handled, interest → CLOSING")
            return "CLOSING"
        else:
            # Остаёмся в текущей стадии или переходим в CLOSING
            logger.info("FSM: Objection handled → CLOSING")
            return "CLOSING"

    elif current_stage == "CLOSING":
        # В CLOSING проверяем ответы про время
        context.closing_attempts += 1

        if context.closing_attempts >= 2:
            # Слишком много попыток — предлагаем чек-лист
            logger.info("FSM: Too many closing attempts → NURTURE")
            return "NURTURE"

        # Остаёмся в CLOSING
        return "CLOSING"

    elif current_stage == "NURTURE":
        # В NURTURE остаёмся или завершаем
        context.nurture_offers_made += 1
        if context.nurture_offers_made >= 2:
            logger.info("FSM: Nurture complete → CLOSED")
            return "CLOSED"
        return "NURTURE"

    elif current_stage == "CLOSED":
        # Диалог завершён
        return "CLOSED"

    # По умолчанию
    logger.warning(f"FSM: Unknown stage {current_stage}, defaulting to VALUE_PITCH")
    return "VALUE_PITCH"


def get_diag_question(context: "TwoLayerContext") -> str:
    """
    Возвращает текст диагностического вопроса (без LLM).

    Args:
        context: Контекст диалога

    Returns:
        Текст вопроса для DIAG_Q стадии
    """
    q_index = context.diag_question_index

    if q_index >= len(DIAG_QUESTIONS):
        return None

    question = DIAG_QUESTIONS[q_index]
    template = question.get("template", "")
    fallback = question.get("fallback", template)

    # Подставляем данные из слотов
    pain = context.slots.get("pain", "")

    if "{pain}" in template and pain:
        return template.format(pain=pain)
    elif "{pain}" in template:
        return fallback

    return template


def get_stage_model(stage: str) -> str:
    """
    Возвращает модель LLM для стадии.

    Args:
        stage: Название FSM-стадии

    Returns:
        Название модели OpenAI
    """
    # Лёгкие стадии — gpt-4.1-mini
    light_stages = ["NEED_SUMMARY", "VALUE_PITCH", "COMMIT_TEST", "PRICE_DISCUSSION"]

    # Сложные стадии — gpt-4.1
    heavy_stages = ["OBJECTION_HANDLING", "CLOSING"]

    if stage in light_stages:
        return "gpt-4.1-mini"
    elif stage in heavy_stages:
        return "gpt-4.1"
    else:
        return "gpt-4.1-mini"


# ============== ANTI-INTERROGATION (защита от допроса) ==============

# Ключевые фразы уточняющих вопросов
CLARIFICATION_KEYWORDS = [
    "уточните",
    "что именно",
    "что вы имеете в виду",
    "что для вас важнее",
    "что для вас приоритетнее",
    "что для вас сейчас приоритетнее",
    "что сейчас важнее",
    "что важнее",
    "что приоритетнее",
    "важнее для вас",
    "приоритетнее для вас",
    "сейчас важнее для вас",
    "о чём речь",
    "о чем речь",
    "какой именно",
    "какая именно",
    "какое именно",
    "какие именно",
    "что конкретно",
    "можете пояснить",
    "поясните",
    "расскажите подробнее",
    "что подразумеваете",
    "в каком смысле",
    "что означает",
    "имеете в виду",
    "чтобы точнее понять",
    "чтобы лучше понять",
    "для уточнения",
    "хотел бы уточнить",
    "хотела бы уточнить",
    "правильно ли я понимаю",
    "правильно понимаю",
    # Альтернативные формы
    "или одновременно",
    "или разгрузить",
    "или ускорить",
]

# Максимум уточняющих вопросов подряд
MAX_CLARIFICATION_ATTEMPTS = 2


def is_clarification_question(text: str) -> bool:
    """
    Определяет, является ли текст уточняющим вопросом.

    Args:
        text: Текст ответа бота

    Returns:
        True если это уточняющий вопрос
    """
    if not text:
        return False

    t = text.lower()

    # Проверяем наличие ключевых фраз
    has_clarification_keyword = any(kw in t for kw in CLARIFICATION_KEYWORDS)

    # Вопрос может заканчиваться на "?" или ":"
    # Также считаем уточняющим если есть ключевая фраза + знак вопроса где-то в тексте
    has_question_indicator = "?" in t or t.rstrip().endswith(":")

    return has_clarification_keyword and has_question_indicator


def is_short_user_reply(text: str) -> bool:
    """
    Определяет, является ли ответ пользователя коротким.
    Короткие ответы типа "да", "профиль", "нет" — сигнал,
    что пользователь не хочет или не может развёрнуто отвечать.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если ответ короткий (1-5 слов)
    """
    if not text:
        return False

    words = text.strip().split()
    return len(words) <= 5


def get_simplified_prompt_instruction(attempts: int) -> str:
    """
    Возвращает инструкцию для LLM в зависимости от количества уточнений.

    Args:
        attempts: Текущее количество уточняющих вопросов

    Returns:
        Инструкция для промпта
    """
    if attempts == 0:
        return ""
    elif attempts == 1:
        return """
⚠️ ВНИМАНИЕ: Ты уже задал 1 уточняющий вопрос.
Если нужен ещё один — задай его МАКСИМАЛЬНО ПРОСТО, как для ребёнка 5 лет:
- Используй простые слова
- Предложи варианты на выбор (А или Б?)
- Можно привести пример
"""
    else:
        return """
🛑 СТОП! Ты уже задал 2 уточняющих вопроса. БОЛЬШЕ НЕЛЬЗЯ.
Сейчас ты ДОЛЖЕН:
1. Перестать спрашивать "уточните", "что вы имеете в виду"
2. Исходя из того, что УЖЕ ИЗВЕСТНО — объясни своими словами
3. Приведи простой пример
4. Дай выбор из 2-3 конкретных вариантов (да/нет или A/Б/В)
5. Двигайся дальше по сценарию (питч → выгода → закрытие)

НЕ ЗАДАВАЙ уточняющих вопросов! Объясни и предложи.
"""


def get_force_move_prompt(context: "TwoLayerContext", user_message: str) -> str:
    """
    Генерирует промпт для принудительного выхода из допроса.
    Используется когда clarification_attempts >= 2.

    Args:
        context: Контекст диалога
        user_message: Последнее сообщение пользователя

    Returns:
        Промпт для LLM
    """
    slots = context.slots
    slots.get("niche", "ваша сфера")
    slots.get("leads_per_month", "ваши заявки")

    return f"""🛑 ПРИНУДИТЕЛЬНЫЙ ВЫХОД ИЗ РЕЖИМА УТОЧНЕНИЙ

Клиент несколько раз не смог или не захотел уточнить формулировку.
Последний ответ клиента: "{user_message}"

ТВОЯ ЗАДАЧА СЕЙЧАС:
1. НЕ ЗАДАВАЙ больше вопросов типа "уточните", "что вы имеете в виду"
2. ПРИМИ то, что клиент сказал, как есть
3. ОБЪЯСНИ по-простому, что ты понял и как бот может помочь
4. ПРЕДЛОЖИ конкретный пример или вариант

ПРИМЕР ХОРОШЕГО ОТВЕТА:
"Понял, речь про {user_message}.
Наш бот умеет автоматически собирать такую информацию от клиентов —
например, спрашивать что именно нужно и записывать в карточку.
Это сэкономит время вашим менеджерам. Интересно посмотреть, как это работает?"

Ответь КОРОТКО (2-3 предложения), БЕЗ уточняющих вопросов, и двигай диалог к следующему шагу.
"""
