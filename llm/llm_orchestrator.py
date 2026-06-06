"""
LLM-оркестратор для AI-продавца SalesWhisper

Жёстко заданные модели:
- BASE_MODEL = "gpt-4.1-mini" — базовая, дешёвая
- SENIOR_MODEL = "gpt-5.1" — старшая, для сложных случаев

При ошибке на gpt-5.1 — автоматический fallback на gpt-4.1-mini.
"""

from typing import List, Dict, Any
from utils.logging import get_logger

logger = get_logger(__name__)

# ЖЁСТКО ЗАДАННЫЕ МОДЕЛИ — НЕ МЕНЯТЬ БЕЗ СОГЛАСОВАНИЯ
BASE_MODEL = "gpt-4.1-mini"
SENIOR_MODEL = "gpt-5.1"
LEADS_THRESHOLD = 100

# ЛПР, которые считаются "серьёзными" для включения старшей модели
SERIOUS_DECISION_MAKERS = {
    "я",
    "self",
    "owner",
    "founder",
    "владелец",
    "директор",
    "собственник",
    "сам",
    "лично я",
}

# Режимы, при которых может включаться старшая модель
SENIOR_MODES = {
    "price_objection",
    "closing",
    "competitor_objection",
    "timing_objection",
}


def select_model(mode: str, context: Dict[str, Any]) -> str:
    """
    Выбрать модель для запроса.

    Логика:
    - По умолчанию: gpt-4.1-mini
    - Для сложных режимов (возражения, закрытие) при leads >= 100 и ЛПР: gpt-5.1

    Args:
        mode: Режим работы (main_pitch, followup, price_objection, etc.)
        context: Контекст с данными клиента

    Returns:
        Название модели
    """
    model = BASE_MODEL

    # Извлекаем данные из контекста
    leads = 0
    dm = ""

    # Контекст может быть вложенным в ai_seller
    ai_ctx = context.get("ai_seller") or context
    if isinstance(ai_ctx, dict):
        leads = ai_ctx.get("leads_per_month") or ai_ctx.get("leads") or 0
        dm = str(ai_ctx.get("decision_maker") or "").lower().strip()

    # Преобразуем leads в int если строка
    if isinstance(leads, str):
        try:
            leads = int(leads)
        except ValueError:
            leads = 0

    # Проверяем условия для старшей модели
    if mode in SENIOR_MODES:
        if leads >= LEADS_THRESHOLD and dm in SERIOUS_DECISION_MAKERS:
            model = SENIOR_MODEL
            logger.info(f"SENIOR_MODEL selected: mode={mode}, leads={leads}, dm={dm}")

    return model


async def generate_response(
    mode: str,
    context: Dict[str, Any],
    messages: List[Dict[str, str]],
    system_prompt: str = None,
    model_override: str = None,
) -> str:
    """
    Генерация ответа через OpenAI API.

    Args:
        mode: Режим работы:
            - "main_pitch" — первый подробный ответ
            - "followup" — обычный ответ на вопросы
            - "price_objection" — возражение по цене
            - "timing_objection" — "не сейчас", "позже"
            - "competitor_objection" — "у нас уже есть"
            - "not_fit_small_leads" — мало лидов
            - "closing" — готовность к следующему шагу
            - FSM stages: need_summary, value_pitch, commit_test, etc.
        context: Слоты и служебные данные
        messages: История диалога [{"role": ..., "content": ...}]
        system_prompt: Системный промпт (опционально)
        model_override: Принудительно использовать эту модель (опционально)

    Returns:
        Текст ответа от LLM
    """
    # Выбираем модель (с учётом override)
    if model_override:
        model = model_override
    else:
        model = select_model(mode, context)

    logger.info(f"AI Seller generate_response: mode={mode}, model={model}")

    # Импортируем основной оркестратор
    from llm.orchestrator import get_orchestrator

    try:
        orchestrator = await get_orchestrator()

        # Формируем user_prompt из последнего сообщения
        user_prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_prompt = msg.get("content", "")
                break

        if not user_prompt:
            user_prompt = "Продолжи диалог."

        # Добавляем mode в context для отладки
        enriched_context = {**context, "ai_seller_mode": mode, "ai_seller_model": model}

        # Первая попытка с выбранной моделью
        response = await orchestrator.generate_response(
            user_prompt=user_prompt,
            context=enriched_context,
            force_model=model,
            system_prompt=system_prompt,
            message_history=messages[:-1] if len(messages) > 1 else None,
        )

        logger.info(
            f"AI Seller response OK: mode={mode}, "
            f"model_requested={model}, model_used={response.model_used}, "
            f"tokens={response.tokens_used}"
        )

        return response.content

    except Exception as e:
        logger.error(f"LLM error with model {model}: {e}")

        # Если ошибка на старшей модели — fallback на базовую
        if model == SENIOR_MODEL:
            logger.warning(f"Fallback from {SENIOR_MODEL} to {BASE_MODEL}")
            try:
                orchestrator = await get_orchestrator()

                response = await orchestrator.generate_response(
                    user_prompt=user_prompt,
                    context=enriched_context,
                    force_model=BASE_MODEL,
                    system_prompt=system_prompt,
                    message_history=messages[:-1] if len(messages) > 1 else None,
                )

                logger.info(f"Fallback successful: model={BASE_MODEL}")
                return response.content

            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")
                return "Извините, возникла техническая ошибка. Можете повторить?"
        else:
            return "Извините, возникла техническая ошибка. Можете повторить?"


def get_model_for_mode(mode: str, context: Dict[str, Any]) -> str:
    """
    Утилита: получить название модели для режима (для логирования).
    """
    return select_model(mode, context)
