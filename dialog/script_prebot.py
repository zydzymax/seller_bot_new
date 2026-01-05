"""
Script Pre-Bot - Скриптовый бот для сбора базовых данных БЕЗ LLM

Собирает обязательные слоты перед передачей в AI-продавца.
Экономит токены на шаблонных вопросах.

Слоты для сбора:
- name: имя клиента
- niche: ниша бизнеса
- leads_per_month: объём заявок
- channels: каналы (сайт, соцсети, мессенджеры)
- current_process: как сейчас обрабатывают заявки
- pain: главная боль
- decision_maker: кто принимает решения
"""

import re
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field

from dialog.intent_helpers import is_agreement, is_rejection, extract_number
from utils.logging import get_logger

logger = get_logger(__name__)


class PreBotState(Enum):
    """Состояния скриптового pre-bot"""
    GREETING = "GREETING"
    ASK_NAME = "ASK_NAME"
    ASK_NICHE = "ASK_NICHE"
    ASK_LEADS = "ASK_LEADS"
    ASK_CHANNELS = "ASK_CHANNELS"
    ASK_CURRENT_PROCESS = "ASK_CURRENT_PROCESS"
    ASK_PAIN = "ASK_PAIN"
    ASK_DECISION_MAKER = "ASK_DECISION_MAKER"
    ASK_AVG_CHECK = "ASK_AVG_CHECK"  # Средний чек (опционально)
    HANDOFF_TO_AI = "HANDOFF_TO_AI"  # Передача в AI-продавца


# Шаблоны сообщений pre-bot (без LLM!)
PREBOT_MESSAGES = {
    PreBotState.GREETING: """Привет! Я Макс из SalesWhisper.

Помогаю бизнесу автоматизировать обработку заявок с помощью ИИ-ботов.

Как вас зовут?""",

    PreBotState.ASK_NAME: "Как вас зовут?",

    PreBotState.ASK_NICHE: "Приятно познакомиться, {name}! В какой нише работает ваш бизнес?",

    PreBotState.ASK_LEADS: "Отлично! Сколько примерно заявок/обращений получаете в месяц?",

    PreBotState.ASK_CHANNELS: "Откуда в основном приходят заявки: сайт, соцсети, мессенджеры, звонки?",

    PreBotState.ASK_CURRENT_PROCESS: "Как сейчас обрабатываете заявки? Менеджеры вручную, есть какой-то бот, или как-то ещё?",

    PreBotState.ASK_PAIN: """Понял! Где больше всего болит:
• медленные ответы
• потерянные заявки
• перегруз менеджеров
• что-то другое?""",

    PreBotState.ASK_DECISION_MAKER: "Кто принимает решения о внедрении новых решений: вы, партнёр, руководитель?",

    PreBotState.ASK_AVG_CHECK: "Чтобы потом понять окупаемость, подскажите примерный средний чек по заказу? Можно вилку, например «3–5 тысяч» или «50–100 тысяч». Если сложно сказать — напишите «не знаю».",
}

# Обязательные слоты для перехода к AI
REQUIRED_SLOTS = [
    "name",
    "niche",
    "leads_per_month",
    "channels",
    "current_process",
    "pain",
    "decision_maker"
]


@dataclass
class PreBotContext:
    """Контекст скриптового pre-bot"""
    state: PreBotState = PreBotState.GREETING
    slots: Dict[str, Any] = field(default_factory=dict)
    message_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "slots": self.slots,
            "message_count": self.message_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PreBotContext":
        return cls(
            state=PreBotState(data.get("state", "GREETING")),
            slots=data.get("slots", {}),
            message_count=data.get("message_count", 0)
        )


class ScriptPreBot:
    """
    Скриптовый pre-bot для сбора базовых данных.

    Работает БЕЗ LLM - только шаблонные сообщения и простой парсинг.
    После сбора всех слотов передаёт управление AI-продавцу.
    """

    def __init__(self):
        # Определяем порядок состояний и какой слот собирает каждое
        self.state_slot_map = {
            PreBotState.ASK_NAME: "name",
            PreBotState.ASK_NICHE: "niche",
            PreBotState.ASK_LEADS: "leads_per_month",
            PreBotState.ASK_CHANNELS: "channels",
            PreBotState.ASK_CURRENT_PROCESS: "current_process",
            PreBotState.ASK_PAIN: "pain",
            PreBotState.ASK_DECISION_MAKER: "decision_maker",
            PreBotState.ASK_AVG_CHECK: "avg_check",  # опциональный слот
        }

        # Опциональные слоты (не блокируют переход к AI)
        self.optional_slots = {"avg_check"}

        # Порядок состояний
        self.state_order = [
            PreBotState.GREETING,
            PreBotState.ASK_NAME,
            PreBotState.ASK_NICHE,
            PreBotState.ASK_LEADS,
            PreBotState.ASK_CHANNELS,
            PreBotState.ASK_CURRENT_PROCESS,
            PreBotState.ASK_PAIN,
            PreBotState.ASK_DECISION_MAKER,
            PreBotState.ASK_AVG_CHECK,  # после ЛПР, перед handoff
            PreBotState.HANDOFF_TO_AI,
        ]

    def process_message(
        self,
        message: str,
        context: PreBotContext
    ) -> Tuple[str, PreBotContext, bool]:
        """
        Обработать сообщение пользователя.

        Returns:
            Tuple[response, updated_context, handoff_to_ai]
            - response: ответ бота
            - updated_context: обновлённый контекст
            - handoff_to_ai: True если нужно передать в AI-продавца
        """
        context.message_count += 1
        message_clean = message.strip()

        logger.info(f"PreBot processing: state={context.state.value}, message='{message_clean[:50]}...'")

        # Обработка GREETING - первое сообщение
        if context.state == PreBotState.GREETING:
            # Первое сообщение от пользователя - отправляем приветствие
            context.state = PreBotState.ASK_NAME
            response = PREBOT_MESSAGES[PreBotState.GREETING]
            return response, context, False

        # Извлекаем данные из сообщения для текущего состояния
        slot_name = self.state_slot_map.get(context.state)
        if slot_name:
            extracted_value = self._extract_slot_value(slot_name, message_clean, context)
            if extracted_value:
                context.slots[slot_name] = extracted_value
                logger.info(f"PreBot extracted: {slot_name} = {extracted_value}")

        # Проверяем, все ли слоты собраны
        if self._all_slots_filled(context):
            context.state = PreBotState.HANDOFF_TO_AI
            return "", context, True  # Передаём в AI

        # Переходим к следующему состоянию
        context.state = self._get_next_state(context)

        # Генерируем ответ
        response = self._get_response(context)

        # Проверяем, нужно ли передавать в AI
        handoff = context.state == PreBotState.HANDOFF_TO_AI

        return response, context, handoff

    def _extract_slot_value(
        self,
        slot_name: str,
        message: str,
        context: PreBotContext
    ) -> Optional[Any]:
        """Извлечь значение слота из сообщения"""
        message_lower = message.lower().strip()

        if slot_name == "name":
            # Имя - берём как есть (первое слово или всё)
            # Убираем "меня зовут", "я" и т.п.
            name = re.sub(r'^(меня зовут|я|привет,?\s*я?)\s*', '', message_lower, flags=re.IGNORECASE)
            name = name.strip().title()
            return name if name else message.strip().title()

        elif slot_name == "niche":
            # Ниша - берём как есть
            return message.strip()

        elif slot_name == "leads_per_month":
            # Число заявок
            number = extract_number(message)
            if number and number >= 1:
                return number
            # Попробуем словесные описания
            if any(w in message_lower for w in ["мало", "немного", "5-10", "до 10"]):
                return 10
            if any(w in message_lower for w in ["много", "сотни", "тысячи"]):
                return 500
            return None

        elif slot_name == "channels":
            # Каналы - извлекаем ключевые слова
            channels = []
            channel_keywords = {
                "сайт": ["сайт", "сайта", "веб", "лендинг"],
                "соцсети": ["соцсет", "инстаграм", "instagram", "вк", "vk", "facebook", "фейсбук"],
                "telegram": ["телеграм", "telegram", "тг"],
                "whatsapp": ["whatsapp", "ватсап", "вотсап", "wa"],
                "звонки": ["звон", "телефон", "колл"],
                "реклама": ["реклам", "таргет", "контекст", "яндекс", "google"],
            }
            for channel, keywords in channel_keywords.items():
                if any(kw in message_lower for kw in keywords):
                    channels.append(channel)

            return ", ".join(channels) if channels else message.strip()

        elif slot_name == "current_process":
            # Как обрабатывают - берём как есть
            return message.strip()

        elif slot_name == "pain":
            # Боль - берём как есть
            return message.strip()

        elif slot_name == "decision_maker":
            # Кто принимает решения
            if any(w in message_lower for w in ["я", "сам", "лично", "самостоятельно"]):
                return "self"
            if any(w in message_lower for w in ["партнёр", "партнер", "совместно", "вместе"]):
                return "partner"
            if any(w in message_lower for w in ["руковод", "директор", "босс", "начальник", "собственник", "владелец"]):
                return "owner"
            return message.strip()

        elif slot_name == "avg_check":
            # Средний чек - опциональный слот
            # Если клиент не знает - пропускаем
            if any(w in message_lower for w in ["не знаю", "сложно", "затрудняюсь", "не скажу", "разный", "зависит"]):
                return None  # Пропускаем слот

            # Пытаемся извлечь число
            number = extract_number(message)
            if number:
                # Корректируем если указано в тысячах
                if number < 1000 and any(w in message_lower for w in ["тыс", "к ", "к.", "тысяч"]):
                    number *= 1000
                return number

            # Если есть диапазон типа "50-100 тысяч" - берём среднее
            range_match = re.search(r'(\d+)\s*[-–]\s*(\d+)', message)
            if range_match:
                low = int(range_match.group(1))
                high = int(range_match.group(2))
                avg = (low + high) // 2
                # Корректируем если тысячи
                if avg < 1000 and any(w in message_lower for w in ["тыс", "тысяч"]):
                    avg *= 1000
                return avg

            return None  # Не удалось извлечь

        return message.strip()

    def _all_slots_filled(self, context: PreBotContext) -> bool:
        """Проверить, все ли обязательные слоты заполнены"""
        for slot in REQUIRED_SLOTS:
            if slot not in context.slots or not context.slots[slot]:
                return False
        return True

    def _get_next_state(self, context: PreBotContext) -> PreBotState:
        """Определить следующее состояние"""
        current_idx = self.state_order.index(context.state)

        # Ищем следующий незаполненный слот
        for next_state in self.state_order[current_idx + 1:]:
            slot_name = self.state_slot_map.get(next_state)

            # Если это HANDOFF - возвращаем его
            if next_state == PreBotState.HANDOFF_TO_AI:
                return next_state

            # Если слот опциональный и мы уже его спрашивали - пропускаем
            if slot_name in self.optional_slots:
                # Если мы УЖЕ в этом состоянии - значит уже спрашивали, идём дальше
                if context.state == next_state:
                    continue
                # Если ещё не спрашивали - спросим
                if slot_name not in context.slots:
                    return next_state
                continue

            # Если обязательный слот не заполнен - идём в это состояние
            if slot_name and slot_name not in context.slots:
                return next_state

        # Все слоты заполнены - передаём в AI
        return PreBotState.HANDOFF_TO_AI

    def _get_response(self, context: PreBotContext) -> str:
        """Получить ответ для текущего состояния"""
        template = PREBOT_MESSAGES.get(context.state, "")

        # Подставляем переменные
        if "{name}" in template and "name" in context.slots:
            template = template.format(name=context.slots["name"])

        return template


# Глобальный инстанс
_prebot: Optional[ScriptPreBot] = None


def get_prebot() -> ScriptPreBot:
    """Получить инстанс pre-bot"""
    global _prebot
    if _prebot is None:
        _prebot = ScriptPreBot()
    return _prebot
