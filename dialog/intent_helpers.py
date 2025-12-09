"""
Intent Helpers - распознавание интентов пользователя
Используется для быстрого определения намерений без вызова LLM
"""

import re
from typing import Optional, Dict, Any


# Паттерны согласия
AGREEMENT_PATTERNS = [
    r"^да$",
    r"^да[,.]",
    r"^давай",
    r"^ок$",
    r"^окей",
    r"^хорошо",
    r"^отлично",
    r"^согласен",
    r"^конечно",
    r"^можно",
    r"^интересно",
    r"^слушаю",
    r"^рассказывай",
    r"^расскажи",
    r"^покажи",
    r"^показывай",
    r"^го$",
    r"^ага",
    r"^угу",
    r"^да,?\s*(давай|расскажи|покажи)",
    r"^ну давай",
    r"^давай расскажи",
    r"^давайте",
    r"^поехали",
    r"^вперед",
    r"^жду",
]

# Паттерны для decision_authority = "owner" (я принимаю решения)
DECISION_MAKER_SELF_PATTERNS = [
    r"^я$",
    r"^я\s+сам",
    r"^я\s+принимаю",
    r"^я\s+решаю",
    r"^это\s+я",
    r"^сам$",
    r"^сам\s+принимаю",
    r"^сам\s+решаю",
    r"^лично\s+я",
    r"^я\s+владелец",
    r"^я\s+собственник",
    r"^я\s+директор",
    r"^я\s+руководитель",
    r"^я\s+основатель",
    r"^я\s+лпр",
    r"^самостоятельно",
    r"^единолично",
]

# Паттерны отказа
REJECTION_PATTERNS = [
    r"^нет$",
    r"^не интересно",
    r"^не надо",
    r"^не нужно",
    r"^спасибо,?\s+не",
    r"^отстань",
    r"^хватит",
    r"^стоп",
]

# Паттерны возражений по цене
PRICE_OBJECTION_KEYWORDS = [
    "дорого",
    "дороговато",
    "слишком дорого",
    "нет такого бюджета",
    "не потянем",
    "не потяну",
    "за такие деньги",
    "много денег",
    "дорогой",
    "высокая цена",
    "слишком много",
    "не по карману",
    "бюджет не позволяет",
    "нет бюджета",
    "слишком дорогой",
    "цена кусается",
    "накладно",
    "не осилим",
    "не осилю",
    "хочу уложиться в",
    "дорого за",
    "дорого даже",
]

# Паттерны возражений по времени
TIMING_OBJECTION_KEYWORDS = [
    "не сейчас",
    "позже",
    "давайте позже",
    "после сезона",
    "нет времени",
    "заняты",
    "сейчас не до этого",
    "не время",
    "потом",
    "может потом",
    "через месяц",
    "через пару месяцев",
    "в следующем году",
    "после нового года",
    "после праздников",
    "сейчас загружены",
    "сейчас аврал",
]

# Паттерны возражений про конкурентов/существующие решения
COMPETITOR_OBJECTION_KEYWORDS = [
    "у нас уже есть",
    "уже есть бот",
    "уже есть подрядчик",
    "с нами уже работает",
    "у нас свой отдел",
    "своя команда",
    "уже работаем с",
    "есть агентство",
    "есть интегратор",
    "уже внедрили",
    "уже используем",
    "свои разработчики",
]

# Паттерны готовности к закрытию (closing)
CLOSING_KEYWORDS = [
    "давай созвонимся",
    "созвонимся",
    "созвон",
    "звонок",
    "позвоните",
    "свяжитесь",
    "напишите мне",
    "мой телефон",
    "мой номер",
    "вот мой контакт",
    "когда можно созвониться",
    "давайте обсудим",
    "готов обсудить",
    "интересно, давайте",
    "хочу попробовать",
    "давайте начнем",
    "как начать",
    "что нужно для старта",
]

# Паттерны выбора времени (ответ на "когда удобно?")
TIME_PREFERENCE_KEYWORDS = [
    "днём",
    "днем",
    "утром",
    "вечером",
    "в будни",
    "в выходные",
    "на неделе",
    "завтра",
    "послезавтра",
    "в понедельник",
    "во вторник",
    "в среду",
    "в четверг",
    "в пятницу",
    "в субботу",
    "в воскресенье",
    "на этой неделе",
    "на следующей неделе",
    "после обеда",
    "до обеда",
    "с утра",
    "ближе к вечеру",
    "первая половина дня",
    "вторая половина дня",
]

# Паттерны контактных данных
CONTACT_PATTERNS = [
    r"@\w+",  # telegram username
    r"\+?\d[\d\s\-\(\)]{8,}",  # телефон
    r"[\w\.-]+@[\w\.-]+\.\w+",  # email
    r"t\.me/\w+",  # telegram link
    r"wa\.me/\d+",  # whatsapp link
]

# Паттерны "не знаю когда" (расплывчатый ответ на вопрос о времени)
DONT_KNOW_TIME_KEYWORDS = [
    "не знаю",
    "не уверен",
    "сложно сказать",
    "затрудняюсь",
    "пока не знаю",
    "не могу сказать",
    "сложно",
    "хз",
    "без понятия",
    "не определился",
    "не определилась",
    "пока не определился",
    "надо подумать",
    "подумаю",
    "я подумаю",
]

# Паттерны расплывчатого периода (без конкретного дня/времени)
VAGUE_PERIOD_KEYWORDS = [
    "на следующей неделе",
    "следующая неделя",
    "на этой неделе",
    "эта неделя",
    "в начале недели",
    "в конце недели",
    "ближе к концу недели",
    "в первой половине недели",
    "во второй половине недели",
    "через неделю",
    "через пару дней",
    "на днях",
    "скоро",
    "в ближайшее время",
]

# Паттерны конкретного дня/времени
SPECIFIC_DAY_PATTERNS = [
    r"в понедельник",
    r"во вторник",
    r"в среду",
    r"в четверг",
    r"в пятницу",
    r"в субботу",
    r"в воскресенье",
    r"понедельник",
    r"вторник",
    r"среда",
    r"среду",
    r"четверг",
    r"пятница",
    r"пятницу",
    r"\d{1,2}\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
    r"\d{1,2}[./]\d{1,2}",  # 15.12, 15/12
]

SPECIFIC_TIME_PATTERNS = [
    r"\d{1,2}[:\.]?\d{0,2}\s*(часов|час|ч\.?)",  # 15 часов, 15:00
    r"в\s+\d{1,2}[:\.]?\d{0,2}",  # в 15:00, в 15
    r"\d{1,2}:\d{2}",  # 15:00
]


def is_agreement(text: str) -> bool:
    """
    Проверяет, является ли текст согласием/подтверждением.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если это согласие
    """
    text_lower = text.lower().strip()

    for pattern in AGREEMENT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True

    return False


def is_decision_maker_self(text: str) -> bool:
    """
    Проверяет, говорит ли пользователь что он сам принимает решения.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если пользователь указывает что он ЛПР
    """
    text_lower = text.lower().strip()

    for pattern in DECISION_MAKER_SELF_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True

    return False


def is_rejection(text: str) -> bool:
    """
    Проверяет, является ли текст отказом.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если это отказ
    """
    text_lower = text.lower().strip()

    for pattern in REJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True

    return False


def is_price_objection(text: str) -> bool:
    """
    Проверяет, является ли текст возражением по цене.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если это возражение по цене (дорого, не потянем и т.п.)
    """
    t = (text or "").lower().strip()
    return any(phrase in t for phrase in PRICE_OBJECTION_KEYWORDS)


def is_timing_objection(text: str) -> bool:
    """
    Проверяет, является ли текст возражением по времени.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если это "не сейчас", "позже", "нет времени" и т.п.
    """
    t = (text or "").lower().strip()
    return any(phrase in t for phrase in TIMING_OBJECTION_KEYWORDS)


def is_competitor_objection(text: str) -> bool:
    """
    Проверяет, является ли текст возражением про конкурентов/существующие решения.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если "у нас уже есть бот/подрядчик" и т.п.
    """
    t = (text or "").lower().strip()
    return any(phrase in t for phrase in COMPETITOR_OBJECTION_KEYWORDS)


def is_closing_signal(text: str) -> bool:
    """
    Проверяет, является ли текст сигналом к закрытию (готовность к созвону/контакту).

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если клиент готов к следующему шагу
    """
    t = (text or "").lower().strip()
    return any(phrase in t for phrase in CLOSING_KEYWORDS)


def is_time_preference(text: str) -> bool:
    """
    Проверяет, является ли текст ответом о предпочтительном времени.
    Например: "днём", "утром", "в будни", "завтра".

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если это выбор времени для созвона
    """
    t = (text or "").lower().strip()
    # Короткий ответ (1-3 слова) содержит время
    if len(t.split()) <= 4:
        return any(phrase in t for phrase in TIME_PREFERENCE_KEYWORDS)
    return False


def is_contact_info(text: str) -> bool:
    """
    Проверяет, содержит ли текст контактные данные.
    Телефон, telegram, email и т.п.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если содержит контакт
    """
    t = (text or "").strip()
    for pattern in CONTACT_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def extract_time_preference(text: str) -> Optional[str]:
    """
    Извлекает предпочтение по времени из текста.

    Returns:
        Строка с временем или None
    """
    t = (text or "").lower().strip()
    for phrase in TIME_PREFERENCE_KEYWORDS:
        if phrase in t:
            return phrase
    return None


def extract_contact(text: str) -> Optional[str]:
    """
    Извлекает контактную информацию из текста.

    Returns:
        Контакт или None
    """
    t = (text or "").strip()
    for pattern in CONTACT_PATTERNS:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def extract_number(text: str) -> Optional[int]:
    """
    Извлекает число из текста (для объема заявок, бюджета и т.п.)

    Args:
        text: Текст сообщения

    Returns:
        Число или None
    """
    # Обработка диапазонов типа "300-400"
    range_match = re.search(r'(\d+)\s*[-–]\s*(\d+)', text)
    if range_match:
        # Берем среднее значение диапазона
        low = int(range_match.group(1))
        high = int(range_match.group(2))
        return (low + high) // 2

    # Обработка чисел с пробелами/разделителями (1 000, 1,000)
    numbers = re.findall(r'\d[\d\s,\.]*\d|\d', text)
    if numbers:
        # Берем первое найденное число
        num_str = numbers[0].replace(' ', '').replace(',', '').replace('.', '')
        try:
            return int(num_str)
        except ValueError:
            pass

    return None


def quick_slot_extraction(text: str, context_question: str = "") -> Dict[str, Any]:
    """
    Быстрое извлечение слотов без LLM.
    Используется для простых случаев.

    Args:
        text: Текст сообщения пользователя
        context_question: Предыдущий вопрос бота (для контекста)

    Returns:
        Словарь с извлеченными слотами
    """
    slots = {}
    text_lower = text.lower().strip()
    context_lower = context_question.lower() if context_question else ""

    # Проверка на decision_authority
    # Если в контексте был вопрос про ЛПР/решения и ответ похож на "я"
    lpr_keywords = ["принимает решения", "лпр", "кто решает", "кто принимает"]
    is_lpr_context = any(kw in context_lower for kw in lpr_keywords)

    if is_lpr_context and is_decision_maker_self(text):
        slots["decision_authority"] = "owner"
    elif is_decision_maker_self(text):
        # Даже без явного контекста, если говорят "я" - это скорее всего ответ на вопрос про ЛПР
        slots["decision_authority"] = "owner"

    # Проверка на число (lead_volume или budget)
    number = extract_number(text)
    if number and number >= 5:
        # Проверяем контекст - спрашивали про бюджет?
        budget_keywords = ["бюджет", "комфортен", "готовы потратить", "стоимость", "цена", "сколько готовы"]
        is_budget_context = any(kw in context_lower for kw in budget_keywords)

        # Проверяем текст на признаки бюджета (рубли, тысячи)
        has_currency = any(word in text_lower for word in ["руб", "₽", "р", "тыс", "к"])

        if is_budget_context or has_currency:
            # Это бюджет
            slots["budget_range"] = number
        else:
            # Проверяем контекст - спрашивали про заявки?
            volume_keywords = ["заявок", "заявки", "лидов", "обращений", "в месяц", "количество"]
            is_volume_context = any(kw in context_lower for kw in volume_keywords)

            if is_volume_context or (number >= 10 and number <= 10000):
                slots["current_lead_volume"] = number

    return slots


def get_intent(text: str) -> str:
    """
    Определяет основной интент сообщения.

    Returns:
        Строка с интентом: "agreement", "rejection", "question", "info", "unknown"
    """
    if is_agreement(text):
        return "agreement"

    if is_rejection(text):
        return "rejection"

    if "?" in text:
        return "question"

    # Если сообщение достаточно длинное - скорее всего это информация
    if len(text.split()) >= 3:
        return "info"

    return "unknown"


# ============== CLOSING HELPERS ==============

def is_dont_know_time(text: str) -> bool:
    """
    Проверяет, говорит ли клиент что не знает когда удобно.
    "не знаю", "сложно сказать", "подумаю" и т.п.

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если клиент не может определиться с временем
    """
    t = (text or "").lower().strip()
    return any(phrase in t for phrase in DONT_KNOW_TIME_KEYWORDS)


def is_vague_time_period(text: str) -> bool:
    """
    Проверяет, указал ли клиент расплывчатый период без конкретного дня.
    "на следующей неделе", "в начале недели", "через пару дней"

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если указан только период, без конкретики
    """
    t = (text or "").lower().strip()
    # Если есть расплывчатый период И нет конкретного дня
    has_vague = any(phrase in t for phrase in VAGUE_PERIOD_KEYWORDS)
    has_specific = has_specific_day_or_time(text)
    return has_vague and not has_specific


def has_specific_day_or_time(text: str) -> bool:
    """
    Проверяет, содержит ли текст конкретный день или время.
    "в понедельник", "15 декабря", "в 15:00"

    Args:
        text: Текст сообщения пользователя

    Returns:
        True если есть конкретный день или время
    """
    t = (text or "").lower().strip()

    # Проверяем конкретные дни
    for pattern in SPECIFIC_DAY_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True

    # Проверяем конкретное время
    for pattern in SPECIFIC_TIME_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True

    return False


def extract_vague_period(text: str) -> Optional[str]:
    """
    Извлекает расплывчатый период из текста.

    Returns:
        Строка с периодом ("следующая неделя", "начало недели") или None
    """
    t = (text or "").lower().strip()
    for phrase in VAGUE_PERIOD_KEYWORDS:
        if phrase in t:
            return phrase
    return None
