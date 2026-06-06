"""
flow_manager.py — Flow Manager для SoVAni AI-продавца с жёсткими бизнес-правилами.

- FSM на Redis с валидацией MOQ/сроков/цен  
- Интеграция с LLM оркестратором и CRM
- Жёсткие guard'ы для бизнес-логики
- Контекст диалога и состояние пользователя
- Telegram bot API для отправки ответов

© SoVAni 2025
"""

import os
import json
import asyncio
import re
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
import yaml

import redis.asyncio as redis
from telegram import Bot
from telegram.error import TelegramError

from utils.logging import get_logger
from utils.supply_mode_parser import parse_supply_mode
from llm.orchestrator import get_orchestrator
from adapters.crm_adapter import get_crm_adapter, ContactData, OrderDetails

logger = get_logger(__name__)


class Slot(str, Enum):
    """Слоты для заполнения данных заказа"""

    PRODUCT_TYPE = "product_type"
    WORK_SCHEME = "work_scheme"
    TOTAL_QUANTITY = "total_quantity"
    COLORS_COUNT = "colors_count"
    FABRIC_COMPOSITION = "fabric_composition"
    CONTACT_PHONE = "contact_phone"


# Обязательные слоты для заказа (в порядке заполнения)
REQUIRED_ORDER_SLOTS = [
    Slot.PRODUCT_TYPE,
    Slot.WORK_SCHEME,
    Slot.COLORS_COUNT,  # сначала цвета, потом количество
    Slot.TOTAL_QUANTITY,
    Slot.FABRIC_COMPOSITION,
    Slot.CONTACT_PHONE,
]


def next_missing_slot(context: "DialogContext") -> str | None:
    """Возвращает имя первого незаполненного слота по скрипту."""
    for slot in REQUIRED_ORDER_SLOTS:
        value = getattr(context, slot.value, None)
        if value in (None, "", 0):
            return slot.value
    return None


# Русские слова-числа → цифры (0-10)
RUS_NUM_WORDS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


def _wordnums_to_digits(text: str) -> str:
    def repl(m):
        w = m.group(0).lower()
        return str(RUS_NUM_WORDS.get(w, w))

    pattern = r"\b(" + "|".join(RUS_NUM_WORDS.keys()) + r")\b"
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


COLOR_INTENT_PATTERNS = [
    r"\bцвет(?:а|ов)?\b",
    r"\bпо\s*цвет",
    r"\bна\s*цвет",
]
COLOR_REPHRASE_PATTERNS = [
    r"\bдавайте\b",
    r"\bсделаем\b",
    r"\bпусть\s*будет\b",
    r"\bуменьш(?:им|у)\b",
    r"\bснизим\b",
    r"\bизменим\b",
]


def _has_color_intent(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in COLOR_INTENT_PATTERNS + COLOR_REPHRASE_PATTERNS)


def _extract_first_int(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    if not match:
        return None
    return int(match.group(0))


class FlowState(Enum):
    """Состояния FSM для воронки продаж"""

    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"
    WORK_SCHEME = "work_scheme"  # Выяснение схемы: под ключ или давальческое
    QUANTITY_COLORS = "quantity_colors"
    MOQ_VALIDATION = "moq_validation"
    FABRIC_DETAILS = "fabric_details"
    PACKAGING_LOGISTICS = "packaging_logistics"
    PRICING_MODE = "pricing_mode"
    CONTACT_COLLECTION = "contact_collection"
    FINALIZATION = "finalization"
    COMPLETED = "completed"


@dataclass
class DialogContext:
    """Контекст диалога пользователя"""

    user_id: int
    chat_id: int
    current_state: FlowState = FlowState.GREETING

    # Данные заказа
    product_type: Optional[str] = None
    work_scheme: Optional[str] = None  # "turnkey" или "client_materials"
    total_quantity: Optional[int] = None
    colors_count: Optional[int] = None
    colors_list: Optional[str] = None  # список цветов через запятую
    quantity_per_color: Optional[int] = None
    quantity_distribution: Optional[str] = None  # распределение по цветам

    fabric_composition: Optional[str] = None
    fabric_density: Optional[str] = None
    hardware_fittings: Optional[str] = None

    packaging_requirements: Optional[str] = None
    labeling_requirements: Optional[str] = None
    logistics_requirements: Optional[str] = None

    pricing_mode: str = "factory_quote"  # factory_quote | client_budget
    client_budget: Optional[float] = None

    # Контактные данные
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    company_name: Optional[str] = None

    # Метаданные
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    warnings: List[str] = None
    greeted_today: bool = False

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь"""
        result = asdict(self)
        result["current_state"] = self.current_state.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DialogContext":
        """Десериализация из словаря"""
        if "current_state" in data:
            data["current_state"] = FlowState(data["current_state"])
        return cls(**data)


class BusinessRulesValidator:
    """Валидатор бизнес-правил"""

    def __init__(self, rules: Dict[str, Any]):
        self.rules = rules

    def validate_moq(
        self, total_qty: int, colors_count: int, work_scheme: str = "turnkey"
    ) -> Dict[str, Any]:
        """Валидация минимального заказа (MOQ)"""
        if work_scheme == "client_materials":
            moq_per_color = self.rules.get("moq", {}).get(
                "client_materials_per_color", 300
            )
        else:
            moq_per_color = self.rules.get("moq", {}).get("turnkey_per_color", 1000)

        if not total_qty or not colors_count:
            return {
                "valid": False,
                "error": "missing_data",
                "message": "Не указано количество или количество цветов",
            }

        quantity_per_color = total_qty // colors_count

        if quantity_per_color < moq_per_color:
            return {
                "valid": False,
                "error": "moq_violation",
                "message": f"При {colors_count} цветах получается {quantity_per_color} шт/цвет, но минимум {moq_per_color} шт/цвет",
                "suggestions": [
                    f"Уменьшить количество цветов до {total_qty // moq_per_color}",
                    f"Увеличить общий объём до {colors_count * moq_per_color} штук",
                    "Разделить заказ на несколько этапов",
                ],
            }

        return {"valid": True, "quantity_per_color": quantity_per_color}


class FlowManager:
    """Менеджер потока диалога с FSM"""

    def __init__(self, redis_url: str = None):
        self.redis_client = redis.from_url(
            redis_url or os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
        )
        self.telegram_bot = None
        if os.getenv("TELEGRAM_TOKEN"):
            self.telegram_bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.llm_orchestrator = None
        self.crm_adapter = None
        self.business_rules = {}
        self.rules_validator = None
        self._load_business_rules()

    def _load_business_rules(self):
        """Загрузка бизнес-правил"""
        rules_path = Path(__file__).parent.parent / "config" / "business_rules.yaml"
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                self.business_rules = yaml.safe_load(f)
                self.rules_validator = BusinessRulesValidator(self.business_rules)
        except Exception as e:
            logger.error(f"Failed to load business rules: {e}")
            self.business_rules = {}
            self.rules_validator = BusinessRulesValidator({})

    async def initialize(self):
        """Инициализация компонентов"""
        self.llm_orchestrator = await get_orchestrator()
        self.crm_adapter = await get_crm_adapter()
        logger.info("Flow manager initialized")

    async def get_context(self, chat_id: int) -> DialogContext:
        """Получение контекста диалога из Redis"""
        key = f"dialog:context:{chat_id}"
        try:
            cached_data = await self.redis_client.get(key)
            if cached_data:
                data = json.loads(cached_data.decode("utf-8"))
                context = DialogContext.from_dict(data)

                # Проверяем, приветствовали ли уже сегодня
                await self._check_greeting_today(context)
                return context
        except Exception as e:
            logger.warning(f"Failed to load context: {e}")

        import time

        context = DialogContext(
            user_id=0, chat_id=chat_id, created_at=time.time(), updated_at=time.time()
        )
        await self._check_greeting_today(context)
        return context

    async def _check_greeting_today(self, context: DialogContext):
        """Проверка и установка флага приветствия на сегодня"""
        from datetime import datetime, timezone, timedelta

        # Московское время
        moscow_tz = timezone(timedelta(hours=3))
        today = datetime.now(moscow_tz).strftime("%Y%m%d")
        greet_key = f"bot:greeted:{context.chat_id}:{today}"

        try:
            # Проверяем, приветствовали ли уже сегодня
            if await self.redis_client.get(greet_key):
                context.greeted_today = True
            else:
                context.greeted_today = False
                # Устанавливаем флаг до конца дня
                tomorrow = datetime.now(moscow_tz).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                ttl = int((tomorrow - datetime.now(moscow_tz)).total_seconds())
                await self.redis_client.setex(greet_key, ttl, "1")
        except Exception as e:
            logger.warning(f"Failed to check greeting flag: {e}")
            context.greeted_today = False

    async def save_context(self, context: DialogContext, update_id: int = None):
        """Сохранение контекста в Redis с атомарным обновлением last_update_id"""
        key = f"dialog:context:{context.chat_id}"
        try:
            import time

            context.updated_at = time.time()
            context.message_count += 1

            # Атомарно сохраняем контекст и last_update_id
            ttl = int(os.getenv("SESSION_TTL_SEC", "3600"))
            pipe = self.redis_client.pipeline()
            pipe.setex(key, ttl, json.dumps(context.to_dict(), default=str))
            if update_id:
                pipe.hset(f"dialog:{context.chat_id}", "last_update_id", update_id)
                pipe.expire(f"dialog:{context.chat_id}", ttl)
            await pipe.execute()
        except Exception as e:
            logger.error(f"Failed to save context: {e}")

    async def send_telegram_message(
        self,
        chat_id: int,
        text: str,
        update_id: int = None,
        keyboard: list = None,
        inline: bool = False,
    ) -> bool:
        """Отправка сообщения в Telegram с защитой от дублирования"""
        if not self.telegram_bot:
            logger.warning("Telegram bot not initialized")
            return False

        # Idempotency guard - prevent duplicate responses
        if update_id is not None:
            try:
                from utils.idempotency_guard import send_once_guard, is_duplicate_text

                # Check if we already sent a response to this update
                can_send = await send_once_guard(chat_id, update_id)
                if not can_send:
                    logger.warning(
                        "duplicate_turn_suppressed",
                        chat_id=chat_id,
                        update_id=update_id,
                    )
                    return True  # Return success to avoid retries

                # Anti-repeat guard - check if this exact text was recently sent
                is_duplicate = await is_duplicate_text(chat_id, text)
                if is_duplicate:
                    logger.warning("duplicate_text_suppressed", chat_id=chat_id)
                    # Try alternative microcopy variant
                    from utils.microcopy import t
                    import random

                    try:
                        if "общее количество" in text:
                            text = t("ask.total_qty") + f" @v{random.choice(['A','B'])}"
                            logger.info(
                                "anti_repeat", original_text=True, variant=text[-2:]
                            )
                    except Exception:
                        pass

            except Exception as guard_error:
                logger.warning(f"Idempotency guard error: {guard_error}")
                # Continue sending if guard fails
                pass

        try:
            # Send typing indicator before message
            import random

            try:
                from telegram.helpers import send_chat_action

                await send_chat_action(chat_id, "typing")
                await asyncio.sleep(random.uniform(0.25, 0.6))  # humanize timing
            except Exception:
                pass  # Typing indicator is optional

            # Build reply markup if keyboard provided
            reply_markup = None
            if keyboard:
                if inline:
                    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

                    reply_markup = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(btn, callback_data=btn)
                                for btn in row
                            ]
                            for row in keyboard
                        ]
                    )
                else:
                    from telegram import ReplyKeyboardMarkup

                    reply_markup = ReplyKeyboardMarkup(
                        keyboard, resize_keyboard=True, one_time_keyboard=True
                    )

            await self.telegram_bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            return True
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def transition_to_state(
        self, context: DialogContext, new_state: FlowState, reason: str = None
    ):
        """Переход FSM в новое состояние"""
        old_state = context.current_state
        context.current_state = new_state

        logger.info(
            f"FSM transition: {old_state.value} -> {new_state.value}",
            chat_id=context.chat_id,
            reason=reason,
        )

    def _parse_russian_numbers(self, text: str) -> List[int]:
        """Парсинг русских числительных"""
        numbers = []
        text_lower = text.lower()

        # Цифры
        for match in re.finditer(r"\b\d+\b", text):
            numbers.append(int(match.group()))

        # Словесные числительные
        word_numbers = {
            "один": 1,
            "одна": 1,
            "два": 2,
            "две": 2,
            "три": 3,
            "четыре": 4,
            "пять": 5,
            "шесть": 6,
            "семь": 7,
            "восемь": 8,
            "девять": 9,
            "десять": 10,
        }

        for word, num in word_numbers.items():
            if word in text_lower:
                numbers.append(num)

        return sorted(list(set(numbers)))

    def _detect_color_intent(self, text: str) -> bool:
        """Определение намерения указать количество цветов"""
        text_lower = text.lower()

        color_markers = [
            "цвет",
            "цвета",
            "цветов",
            "расцветка",
            "расцветки",
            "вариант",
            "варианта",
            "оттенок",
            "оттенка",
            "тон",
            "тона",
            "колер",
            "колора",
        ]

        change_markers = [
            "давайте",
            "сделаем",
            "пусть будет",
            "хочу",
            "нужно",
            "лучше",
            "исправляю",
            "поправляю",
            "меняю на",
            "нет",
            "не",
        ]

        has_color_marker = any(marker in text_lower for marker in color_markers)
        has_change_marker = any(marker in text_lower for marker in change_markers)

        return has_color_marker or has_change_marker

    def extract_order_data(
        self, message_text: str, context: DialogContext
    ) -> Dict[str, Any]:
        """Извлечение данных заказа из сообщения"""
        extracted = {}
        text_lower = message_text.lower()

        # Извлечение чисел (включая русские числительные)
        numbers = self._parse_russian_numbers(message_text)

        # Определение типа продукта
        product_keywords = {
            "толстовк": "толстовка",
            "худи": "худи",
            "футболк": "футболка",
            "пижам": "пижама",
            "костюм": "костюм",
            "свитшот": "свитшот",
            "майк": "майка",
            "шорт": "шорты",
        }

        for keyword, product_type in product_keywords.items():
            if keyword in text_lower:
                extracted["product_type"] = product_type
                break

        # Извлечение количества и цветов
        if context.current_state == FlowState.QUANTITY_COLORS:
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: используем новый парсер с переопределением
            norm_text = _wordnums_to_digits(message_text)
            maybe_n = _extract_first_int(
                message_text
            )  # используем оригинальный текст для чисел
            if _has_color_intent(norm_text) and (maybe_n and maybe_n > 0):
                # КЛЮЧ: в этом состоянии всегда разрешаем переопределять colors_count
                extracted["colors_count"] = maybe_n
            elif maybe_n and maybe_n > 0:
                # Фолбэк-логика как раньше
                if context.colors_count is None:
                    extracted["colors_count"] = maybe_n
                elif context.total_quantity is None:
                    extracted["total_quantity"] = maybe_n

        # Извлечение чисел для других состояний
        numbers = [int(x) for x in re.findall(r"\b\d+\b", message_text)]
        if numbers and context.current_state != FlowState.QUANTITY_COLORS:
            # Общая логика для других состояний
            if any(word in text_lower for word in ["цвет", "расцветка", "вариант"]):
                extracted["colors_count"] = numbers[0]
            elif len(numbers) == 1:
                extracted["total_quantity"] = numbers[0]
            elif len(numbers) >= 2:
                extracted["total_quantity"] = numbers[0]
                extracted["colors_count"] = numbers[1]

        # Извлечение информации о тканях
        fabric_keywords = {
            "хлопок": "хлопок",
            "полиэстер": "полиэстер",
            "вискоза": "вискоза",
            "шелк": "шелк",
            "мульберри": "шелк мульберри",
            "сатин": "сатин",
            "футер": "футер",
            "трикотаж": "трикотаж",
            "флис": "флис",
        }

        for keyword, fabric_type in fabric_keywords.items():
            if keyword in text_lower:
                if "100" in text_lower:
                    extracted["fabric_composition"] = f"100% {fabric_type}"
                else:
                    extracted["fabric_composition"] = fabric_type
                break

        # Извлечение информации о цветах
        color_keywords = [
            "красн",
            "белый",
            "черн",
            "син",
            "зелен",
            "желт",
            "сер",
            "беж",
            "розов",
            "фиолет",
            "корич",
            "оранж",
        ]
        found_colors = []
        for color in color_keywords:
            if color in text_lower:
                found_colors.append(color)

        if found_colors:
            extracted["colors_list"] = ", ".join(found_colors)

        # Извлечение распределения количества
        if any(
            word in text_lower
            for word in ["распределение", "по цветам", "остальные по"]
        ):
            extracted["quantity_distribution"] = message_text

        # Извлечение контактов
        if any(word in text_lower for word in ["телефон", "+7", "8-9", "89", "номер"]):
            phone_match = re.search(
                r"(?:\+7|8)[\s\-]?\(?(\d{3})\)?[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})",
                message_text,
            )
            if phone_match:
                extracted["contact_phone"] = phone_match.group(0)

        # Извлечение имени
        name_patterns = [
            r"меня зовут\s+([а-яё]+)",
            r"я\s+([а-яё]+)",
            r"имя\s+([а-яё]+)",
        ]
        for pattern in name_patterns:
            match = re.search(pattern, text_lower)
            if match:
                extracted["contact_name"] = match.group(1).capitalize()
                break

        return extracted

    async def process_fsm_logic(
        self, context: DialogContext, message_text: str
    ) -> Dict[str, Any]:
        """Логика FSM переходов"""
        current_state = context.current_state
        extracted_data = self.extract_order_data(message_text, context)

        # Обновляем контекст данными из сообщения
        for key, value in extracted_data.items():
            if hasattr(context, key) and value:
                setattr(context, key, value)

        result = {"state_changed": False, "validation_errors": []}

        # FSM переходы
        if current_state == FlowState.GREETING:
            if extracted_data.get("product_type"):
                await self.transition_to_state(
                    context, FlowState.PRODUCT_INQUIRY, "product_mentioned"
                )
                result["state_changed"] = True
            elif any(
                word in message_text.lower() for word in ["привет", "добро", "здравств"]
            ):
                # Остаемся в greeting
                pass
            else:
                await self.transition_to_state(
                    context, FlowState.PRODUCT_INQUIRY, "general_inquiry"
                )
                result["state_changed"] = True

        elif current_state == FlowState.PRODUCT_INQUIRY:
            if extracted_data.get("product_type") or context.product_type:
                # Продукт уже есть в контексте, переходим к схеме работы
                await self.transition_to_state(
                    context, FlowState.WORK_SCHEME, "product_specified"
                )
                result["state_changed"] = True

        elif current_state == FlowState.WORK_SCHEME:
            mode = parse_supply_mode(message_text)
            if mode:
                # АТОМАРНО сохранить и перейти на следующий шаг
                work_scheme = "turnkey" if mode == "pod_kluch" else "client_materials"
                context.work_scheme = work_scheme

                await self.transition_to_state(
                    context, FlowState.QUANTITY_COLORS, "work_scheme_specified"
                )
                result["state_changed"] = True

                # Короткое подтверждение + следующий вопрос
                confirm = (
                    "Записала режим: под ключ ✅"
                    if mode == "pod_kluch"
                    else "Записала режим: давальческое сырьё ✅"
                )
                result["work_scheme_confirmed"] = True
                result["confirm_message"] = confirm
            else:
                # Анти-повтор: проверяем, не задавали ли уже этот вопрос
                repeat_key = f"supply_mode_{context.user_id}"
                if (
                    hasattr(context, "_repeat_cache")
                    and context._repeat_cache.get(repeat_key) == message_text
                ):
                    result["oops_repeat"] = True
                else:
                    if not hasattr(context, "_repeat_cache"):
                        context._repeat_cache = {}
                    context._repeat_cache[repeat_key] = message_text

        elif current_state == FlowState.QUANTITY_COLORS:
            # ИСПРАВЛЕНИЕ: Проверяем переопределение product_type
            if extracted_data.get("product_type"):
                context.product_type = extracted_data["product_type"]
                # Возвращаемся к схеме работы для подтверждения
                await self.transition_to_state(
                    context, FlowState.WORK_SCHEME, "product_redefined"
                )
                result["state_changed"] = True
            elif extracted_data.get("total_quantity") and extracted_data.get(
                "colors_count"
            ):
                context.total_quantity = extracted_data["total_quantity"]
                context.colors_count = extracted_data["colors_count"]

                # MOQ валидация
                moq_result = self.rules_validator.validate_moq(
                    context.total_quantity,
                    context.colors_count,
                    context.work_scheme or "turnkey",
                )

                if moq_result["valid"]:
                    context.quantity_per_color = moq_result["quantity_per_color"]
                    await self.transition_to_state(
                        context, FlowState.FABRIC_DETAILS, "moq_valid"
                    )
                    result["state_changed"] = True
                else:
                    await self.transition_to_state(
                        context, FlowState.MOQ_VALIDATION, "moq_violation"
                    )
                    result["validation_errors"] = [moq_result]
                    result["state_changed"] = True
            elif extracted_data.get("colors_count"):
                # Только количество цветов - ждем общее количество
                context.colors_count = extracted_data["colors_count"]
                # Проверяем, есть ли теперь оба значения
                if context.total_quantity:
                    moq_result = self.rules_validator.validate_moq(
                        context.total_quantity,
                        context.colors_count,
                        context.work_scheme or "turnkey",
                    )
                    if moq_result["valid"]:
                        context.quantity_per_color = moq_result["quantity_per_color"]
                        await self.transition_to_state(
                            context, FlowState.FABRIC_DETAILS, "moq_valid"
                        )
                        result["state_changed"] = True
                    else:
                        await self.transition_to_state(
                            context, FlowState.MOQ_VALIDATION, "moq_violation"
                        )
                        result["validation_errors"] = [moq_result]
                        result["state_changed"] = True
            elif extracted_data.get("total_quantity"):
                # Только общее количество - ждем цвета
                context.total_quantity = extracted_data["total_quantity"]

                # Отправляем подтверждение парсинга количества
                from utils.microcopy import t

                confirm_msg = t("confirm.qty_saved", qty=context.total_quantity)
                result["quantity_confirmed"] = True
                result["confirm_message"] = confirm_msg

                # Проверяем, есть ли теперь оба значения
                if context.colors_count:
                    moq_result = self.rules_validator.validate_moq(
                        context.total_quantity,
                        context.colors_count,
                        context.work_scheme or "turnkey",
                    )
                    if moq_result["valid"]:
                        context.quantity_per_color = moq_result["quantity_per_color"]
                        await self.transition_to_state(
                            context, FlowState.FABRIC_DETAILS, "moq_valid"
                        )
                        result["state_changed"] = True
                    else:
                        await self.transition_to_state(
                            context, FlowState.MOQ_VALIDATION, "moq_violation"
                        )
                        result["validation_errors"] = [moq_result]
                        result["state_changed"] = True

        elif current_state == FlowState.MOQ_VALIDATION:
            # Обработка изменений после нарушения MOQ
            if extracted_data.get("total_quantity") or extracted_data.get(
                "colors_count"
            ):
                total = extracted_data.get("total_quantity", context.total_quantity)
                colors = extracted_data.get("colors_count", context.colors_count)

                if total and colors:
                    moq_result = self.rules_validator.validate_moq(
                        total, colors, context.work_scheme or "turnkey"
                    )
                    if moq_result["valid"]:
                        context.total_quantity = total
                        context.colors_count = colors
                        context.quantity_per_color = moq_result["quantity_per_color"]
                        await self.transition_to_state(
                            context, FlowState.FABRIC_DETAILS, "moq_corrected"
                        )
                        result["state_changed"] = True
                    else:
                        result["validation_errors"] = [moq_result]

        elif current_state == FlowState.FABRIC_DETAILS:
            fabric_mentioned = any(
                word in message_text.lower()
                for word in [
                    "хлопок",
                    "полиэстер",
                    "100%",
                    "%",
                    "состав",
                    "ткань",
                    "футер",
                ]
            )
            if fabric_mentioned:
                context.fabric_composition = message_text
                await self.transition_to_state(
                    context, FlowState.PACKAGING_LOGISTICS, "fabric_specified"
                )
                result["state_changed"] = True

        elif current_state == FlowState.PACKAGING_LOGISTICS:
            packaging_mentioned = any(
                word in message_text.lower()
                for word in ["упаковка", "пакет", "коробка", "этикетка", "бирка"]
            )
            if packaging_mentioned or "нет" in message_text.lower():
                context.packaging_requirements = message_text
                await self.transition_to_state(
                    context, FlowState.PRICING_MODE, "packaging_specified"
                )
                result["state_changed"] = True

        elif current_state == FlowState.PRICING_MODE:
            if any(
                word in message_text.lower()
                for word in ["бюджет", "рублей", "тысяч", "стоит"]
            ):
                context.pricing_mode = "client_budget"
                budget_numbers = [int(x) for x in re.findall(r"\b\d+\b", message_text)]
                if budget_numbers:
                    context.client_budget = max(
                        budget_numbers
                    )  # Берем максимальное число как бюджет
                await self.transition_to_state(
                    context, FlowState.CONTACT_COLLECTION, "budget_specified"
                )
                result["state_changed"] = True
            elif any(
                word in message_text.lower()
                for word in ["расчет", "рассчитайте", "цена"]
            ):
                context.pricing_mode = "factory_quote"
                await self.transition_to_state(
                    context, FlowState.CONTACT_COLLECTION, "quote_requested"
                )
                result["state_changed"] = True

        elif current_state == FlowState.CONTACT_COLLECTION:
            if extracted_data.get("contact_phone"):
                context.contact_phone = extracted_data["contact_phone"]
                if extracted_data.get("contact_name"):
                    context.contact_name = extracted_data["contact_name"]
                await self.transition_to_state(
                    context, FlowState.FINALIZATION, "contacts_provided"
                )
                result["state_changed"] = True
            elif extracted_data.get("contact_name") and not context.contact_phone:
                context.contact_name = extracted_data["contact_name"]
                # Остаемся в состоянии, ждем телефон

        elif current_state == FlowState.FINALIZATION:
            # Финализация - переход в completed или возврат к редактированию
            if any(
                word in message_text.lower()
                for word in ["да", "верно", "правильно", "согласен"]
            ):
                await self.transition_to_state(
                    context, FlowState.COMPLETED, "confirmed"
                )
                result["state_changed"] = True
            elif any(
                word in message_text.lower()
                for word in ["изменить", "поправить", "другой"]
            ):
                # Определяем что изменить и возвращаемся в соответствующее состояние
                if any(word in message_text.lower() for word in ["количество", "цвет"]):
                    await self.transition_to_state(
                        context, FlowState.QUANTITY_COLORS, "correction_requested"
                    )
                elif any(word in message_text.lower() for word in ["ткань", "состав"]):
                    await self.transition_to_state(
                        context, FlowState.FABRIC_DETAILS, "correction_requested"
                    )
                else:
                    await self.transition_to_state(
                        context, FlowState.CONTACT_COLLECTION, "correction_requested"
                    )
                result["state_changed"] = True

        return result

    async def process_completed_order(self, context: DialogContext) -> Dict[str, Any]:
        """Обработка завершенного заказа - отправка в CRM"""
        try:
            # Подготовка контактных данных
            contact_data = ContactData(
                name=context.contact_name,
                phone=context.contact_phone,
                email=context.contact_email,
                company=context.company_name,
            )

            # Подготовка данных заказа
            order_details = OrderDetails(
                product_type=context.product_type,
                total_quantity=context.total_quantity,
                colors_count=context.colors_count,
                fabric_composition=context.fabric_composition,
                pricing_mode=context.pricing_mode,
                estimated_value=context.client_budget,
            )

            # Внешний ID для идемпотентности
            external_id = (
                f"tg_{context.chat_id}_{context.user_id}_{int(context.created_at)}"
            )

            # Отправка в CRM
            crm_result = await self.crm_adapter.create_or_update_lead(
                external_id=external_id,
                contact_data=contact_data,
                order_details=order_details,
                source="telegram_bot",
            )
            crm_success = bool(crm_result.get("success")) or crm_result.get(
                "status"
            ) in {"success", "cached"}

            logger.info(
                "Order sent to CRM",
                chat_id=context.chat_id,
                external_id=external_id,
                crm_success=crm_success,
                contact_id=crm_result.get("contact_id"),
                lead_id=crm_result.get("lead_id"),
            )

            return {
                "crm_success": crm_success,
                "crm_result": crm_result,
                "external_id": external_id,
            }

        except Exception as e:
            logger.error(
                f"Failed to process completed order: {e}",
                chat_id=context.chat_id,
                exc_info=True,
            )
            return {"crm_success": False, "error": str(e)}

    def get_order_summary(self, context: DialogContext) -> str:
        """Генерация сводки заказа для финализации"""
        summary_lines = ["📋 **Сводка вашего заказа:**", ""]

        if context.product_type:
            summary_lines.append(f"🔸 **Продукт:** {context.product_type}")

        if context.total_quantity and context.colors_count:
            quantity_per_color = context.total_quantity // context.colors_count
            summary_lines.append(
                f"🔸 **Количество:** {context.total_quantity} шт ({context.colors_count} цвета по {quantity_per_color} шт)"
            )

        if context.fabric_composition:
            summary_lines.append(f"🔸 **Ткань:** {context.fabric_composition}")

        if context.packaging_requirements:
            summary_lines.append(f"🔸 **Упаковка:** {context.packaging_requirements}")

        if context.pricing_mode == "client_budget" and context.client_budget:
            summary_lines.append(f"🔸 **Бюджет:** {context.client_budget:,.0f} руб.")
        elif context.pricing_mode == "factory_quote":
            summary_lines.append("🔸 **Ценообразование:** Расчет от завода")

        if context.contact_name:
            summary_lines.append(f"🔸 **Контакт:** {context.contact_name}")

        if context.contact_phone:
            summary_lines.append(f"🔸 **Телефон:** {context.contact_phone}")

        summary_lines.extend(["", "Все данные верны?"])

        return "\n".join(summary_lines)

    async def _get_slot_based_response(
        self, context: DialogContext, message_text: str
    ) -> Optional[dict]:
        """Системная логика ответов на основе слотов (идемпотентная)"""
        from utils.microcopy import t
        from utils.message_shaper import shape
        from utils.oops_handler import should_oops, build_oops

        # Определяем следующий отсутствующий слот
        missing_slot = next_missing_slot(context)

        if missing_slot is None:
            # Все ключевые слоты собраны - MOQ валидация
            if context.colors_count and context.total_quantity:
                qty_per_color = context.total_quantity // context.colors_count
                min_qty = 1000 if context.work_scheme == "turnkey" else 300

                if qty_per_color >= min_qty:
                    # Проверяем, нужно ли предложить handoff
                    from telegram.handoff import should_offer_handoff

                    if should_offer_handoff(context):
                        summary_text = shape(
                            t(
                                "summary",
                                qty=context.total_quantity,
                                colors=context.colors_count,
                            ),
                            "summary",
                        )
                        return {
                            "text": summary_text,
                            "keyboard": [
                                ["Изменить количество", "Изменить цвета"],
                                ["Связать с менеджером"],
                            ],
                            "inline": True,
                        }
                    else:
                        return {
                            "text": shape(
                                t(
                                    "summary",
                                    qty=context.total_quantity,
                                    colors=context.colors_count,
                                ),
                                "summary",
                            ),
                            "keyboard": [
                                ["Изменить количество", "Изменить цвета"],
                                ["Продолжить"],
                            ],
                            "inline": True,
                        }
                else:
                    return {
                        "text": shape(
                            f"При {context.colors_count} цветах получается {qty_per_color} шт на цвет/модель, но "
                            + t("confirm.moq_hint", moq=min_qty),
                            "ask",
                        )
                    }

        # Спрашиваем ТОЛЬКО следующий пропущенный слот
        if missing_slot == Slot.PRODUCT_TYPE:
            # ГОРЯЧИЙ ПАТЧ: Приветствие строго по скрипту (Алёна, SoVAni)
            greeting = t("greetings")
            if isinstance(greeting, list):
                greeting = greeting[0]
            return {"text": greeting + "\n\nЧто будем отшивать?"}

        elif missing_slot == Slot.WORK_SCHEME:
            # Проверяем анти-повтор для supply_mode
            try:
                should_oops_flag = await should_oops(context.chat_id, "ask.supply_mode")
            except Exception:
                should_oops_flag = False

            if should_oops_flag:
                ask_text = "Уточните, пожалуйста: под ключ или из вашего сырья?\nВарианты: под ключ • давальческое сырьё • Другое"
                logger.info("anti_repeat", step="supply_mode", reformulated=1)
                return {"text": shape(ask_text, "ask")}
            else:
                ask_text = "Работаем полностью под ключ или из вашего сырья? Напишите текстом.\nВарианты: под ключ • давальческое сырьё • Другое"
                return {"text": shape(ask_text, "ask")}

        elif missing_slot == Slot.COLORS_COUNT:
            if context.work_scheme:
                min_qty = "1000" if context.work_scheme == "turnkey" else "300"
                text = "Сколько цветов планируете? (1–5)\nВарианты: 1 • 2 • 3 • 4 • 5"
                return {"text": shape(text, "ask")}
            else:
                text = "Сколько цветов планируете? (1–5)\nВарианты: 1 • 2 • 3 • 4 • 5"
                return {"text": shape(text, "ask")}

        elif missing_slot == Slot.TOTAL_QUANTITY:
            # Проверяем oops-handler
            import asyncio

            chat_id = context.chat_id

            try:
                should_oops_result = asyncio.create_task(
                    should_oops(chat_id, "ask.total_qty")
                )
                should_oops_flag = await should_oops_result
            except Exception:
                should_oops_flag = False

            if should_oops_flag:
                oops_response = build_oops("ask.total_qty")
                return {
                    "text": shape(oops_response["text"], "ask"),
                    "keyboard": oops_response["keyboard"],
                }

            import random

            variant = random.choice(["A", "B"])
            logger.info("ux_variant", step="ask.total_qty", variant=variant)

            if context.colors_count and context.work_scheme:
                min_total = context.colors_count * (
                    1000 if context.work_scheme == "turnkey" else 300
                )
                text = (
                    t("confirm.colors_saved", colors=context.colors_count)
                    + " "
                    + t("ask.total_qty")
                    + f" Минимум {min_total} шт. @v{variant}"
                )
                return {"text": shape(text, "ask")}
            else:
                return {"text": shape(t("ask.total_qty") + f" @v{variant}", "ask")}

        elif missing_slot == Slot.FABRIC_COMPOSITION:
            return {
                "text": "Какой состав ткани планируете? (хлопок, полиэстер, футер и т.д.)"
            }

        elif missing_slot == Slot.CONTACT_PHONE:
            return {
                "text": "Отлично! Оставьте, пожалуйста, ваш номер телефона для связи."
            }

        return None

    def _get_forced_response(
        self, context: DialogContext, message_text: str, extracted_data: Dict[str, Any]
    ) -> str:
        """
        Legacy sync helper kept for compatibility with older tests/callers.
        """
        if context.current_state == FlowState.WORK_SCHEME and context.work_scheme:
            return "Записала режим. Сколько цветов планируете? (1–5)"

        if context.colors_count and context.total_quantity:
            qty_per_color = context.total_quantity // context.colors_count
            min_qty = 1000 if context.work_scheme == "turnkey" else 300
            if qty_per_color >= min_qty:
                return (
                    f"Отлично, минимумы соблюдены: {qty_per_color} шт на цвет "
                    f"при {context.colors_count} цветах."
                )
            return (
                f"При {context.colors_count} цветах получается {qty_per_color} шт на цвет, "
                f"минимум {min_qty} шт."
            )

        return "Уточните, пожалуйста, параметры заказа."

    async def process_message(
        self,
        user_info: Dict[str, Any],
        message_text: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Основная обработка сообщения через FSM"""
        chat_id = user_info["chat_id"]
        update_id = update.get("update_id")

        try:
            context = await self.get_context(chat_id)
            context.user_id = user_info.get("user_id", 0)

            # Fast-path: проверяем, можно ли извлечь много слотов сразу
            from utils.intent_fastlane import extract_slots, fastlane_decision
            from utils.message_shaper import shape

            if context.current_state in [
                FlowState.GREETING,
                FlowState.PRODUCT_INQUIRY,
                FlowState.WORK_SCHEME,
            ]:
                slots = extract_slots(message_text)
                if fastlane_decision(slots):
                    logger.info("fastlane_triggered", slots=slots, chat_id=chat_id)

                    # Заполняем контекст извлеченными данными
                    if slots.get("product"):
                        context.product_type = slots["product"]
                    if slots.get("supply_mode"):
                        context.work_scheme = (
                            "turnkey"
                            if "под ключ" in slots["supply_mode"]
                            else "client_materials"
                        )
                    if slots.get("colors"):
                        context.colors_count = slots["colors"]
                    if slots.get("qty"):
                        context.total_quantity = slots["qty"]

                    # Переходим к состоянию сбора недостающих данных
                    await self.transition_to_state(
                        context, FlowState.QUANTITY_COLORS, "fastlane"
                    )

                    # Формируем ответ с недостающими полями
                    missing_fields = []
                    if not context.total_quantity:
                        missing_fields.append("количество")
                    if not context.colors_count:
                        missing_fields.append("количество цветов")
                    if not context.work_scheme:
                        missing_fields.append("схему работы")

                    if missing_fields:
                        response_text = (
                            f"Отлично! Понял: {context.product_type or 'изделие'}"
                        )
                        if context.total_quantity:
                            response_text += f", {context.total_quantity} шт"
                        response_text += f". Уточните: {', '.join(missing_fields)}."

                        await self.send_telegram_message(
                            chat_id, shape(response_text, "ask"), update_id
                        )
                        await self.save_context(context, update_id)
                        return {
                            "state": context.current_state.value,
                            "response_sent": True,
                            "fastlane": True,
                        }

            # FSM обработка
            fsm_result = await self.process_fsm_logic(context, message_text)

            # Специальная обработка состояний
            if context.current_state == FlowState.FINALIZATION and not fsm_result.get(
                "validation_errors"
            ):
                # Отправляем сводку заказа
                summary_text = self.get_order_summary(context)
                await self.send_telegram_message(chat_id, summary_text, update_id)
                await self.save_context(context, update_id)

                return {
                    "state": context.current_state.value,
                    "response_sent": True,
                    "order_summary_sent": True,
                    "fsm_processed": True,
                }

            elif context.current_state == FlowState.COMPLETED:
                # Обработка завершенного заказа
                crm_result = await self.process_completed_order(context)

                if crm_result.get("crm_success"):
                    completion_text = f"""✅ **Отлично! Заказ принят.**

📝 Ваша заявка передана менеджеру.
🆔 ID заявки: `{crm_result.get('external_id', 'N/A')}`

📞 **Что дальше:**
• Менеджер свяжется с вами в течение 2-3 часов
• Подготовит коммерческое предложение
• Обсудит детали и сроки производства

Спасибо за обращение в SoVAni! 🙏"""
                else:
                    completion_text = f"""⚠️ **Заказ принят, но возникли технические сложности.**

Ваши данные сохранены:
• Продукт: {context.product_type or 'не указан'}
• Количество: {context.total_quantity or 'не указано'}
• Контакт: {context.contact_phone or 'не указан'}

Менеджер свяжется с вами в ближайшее время."""

                await self.send_telegram_message(chat_id, completion_text, update_id)
                await self.save_context(context, update_id)

                return {
                    "state": context.current_state.value,
                    "response_sent": True,
                    "order_completed": True,
                    "crm_success": crm_result.get("crm_success", False),
                    "fsm_processed": True,
                }

            # Если есть ошибки валидации (например, MOQ), обрабатываем их
            if fsm_result.get("validation_errors"):
                for error in fsm_result["validation_errors"]:
                    if error.get("error") == "moq_violation":
                        response_text = f"""❌ {error['message']}

📋 **Предлагаю варианты:**
• {error['suggestions'][0]}
• {error['suggestions'][1]}
• {error['suggestions'][2]}

Как будем корректировать заказ?"""

                        await self.send_telegram_message(
                            chat_id, response_text, update_id
                        )
                        await self.save_context(context, update_id)

                        return {
                            "state": context.current_state.value,
                            "response_sent": True,
                            "validation_error": True,
                            "fsm_processed": True,
                        }

            # Проверяем callback для handoff
            if message_text == "Связать с менеджером":
                from telegram.handoff import handoff_to_manager

                username = user_info.get("username", "")
                await handoff_to_manager(chat_id, context, username)
                return {
                    "state": context.current_state.value,
                    "response_sent": True,
                    "handoff": True,
                }

            # Отправляем подтверждения, если они есть
            if fsm_result.get("quantity_confirmed"):
                confirm_msg = shape(fsm_result.get("confirm_message", ""), "confirm")
                await self.send_telegram_message(chat_id, confirm_msg, update_id)

            if fsm_result.get("work_scheme_confirmed"):
                confirm_msg = fsm_result.get("confirm_message", "")
                await self.send_telegram_message(chat_id, confirm_msg, update_id)

            # ГОРЯЧИЙ ПАТЧ: Если FSM обработал сообщение и изменил состояние - не вызываем slot_response
            if not fsm_result.get("state_changed") and not fsm_result.get(
                "work_scheme_confirmed"
            ):
                # СИСТЕМНАЯ ЛОГИКА НА ОСНОВЕ СЛОТОВ - идемпотентная, без повторов
                slot_response = await self._get_slot_based_response(
                    context, message_text
                )

                if slot_response:
                    # Используем системный ответ на основе слотов
                    text = (
                        slot_response.get("text", slot_response)
                        if isinstance(slot_response, dict)
                        else slot_response
                    )
                    keyboard = (
                        slot_response.get("keyboard")
                        if isinstance(slot_response, dict)
                        else None
                    )
                    inline = (
                        slot_response.get("inline", False)
                        if isinstance(slot_response, dict)
                        else False
                    )
                    await self.send_telegram_message(
                        chat_id, text, update_id, keyboard, inline
                    )
                    await self.save_context(context, update_id)

                    return {
                        "state": context.current_state.value,
                        "response_sent": True,
                        "slot_based_response": True,
                        "fsm_processed": True,
                        "missing_slot": next_missing_slot(context),
                    }

            # Генерация ответа через LLM с полным снимком контекста
            llm_context = {
                "user_id": context.user_id,
                "state": context.current_state.value,
                "slots": {
                    "product_type": context.product_type,
                    "work_scheme": context.work_scheme,
                    "total_quantity": context.total_quantity,
                    "colors_count": context.colors_count,
                    "fabric_composition": context.fabric_composition,
                    "contact_name": context.contact_name,
                    "contact_phone": context.contact_phone,
                },
                "script": {
                    "required_order": [s.value for s in REQUIRED_ORDER_SLOTS],
                    "next_missing": next_missing_slot(context),
                },
                "flags": {"greeted_today": getattr(context, "greeted_today", False)},
                "business_rules": {
                    "min_qty_turnkey": 1000,
                    "min_qty_client_materials": 300,
                },
                "hint": f"Следующий недостающий параметр: {next_missing_slot(context) or 'все собрано'}",
            }

            llm_response = await self.llm_orchestrator.generate_response(
                message_text, context=llm_context
            )

            # Отправка ответа
            response_sent = await self.send_telegram_message(
                chat_id, llm_response.content, update_id
            )

            # Сохранение контекста
            await self.save_context(context, update_id)

            return {
                "state": context.current_state.value,
                "response_sent": response_sent,
                "llm_used": llm_response.model_used,
                "fsm_processed": True,
                "state_changed": fsm_result.get("state_changed", False),
            }

        except Exception as e:
            # ГОРЯЧИЙ ПАТЧ: Error-guard - мягкий ответ + продолжение сценария
            logger.error(f"Flow processing error: {e}", exc_info=True)

            try:
                # Пытаемся восстановить контекст и дать мягкий ответ
                context = await self.get_context(chat_id)
                fallback_text = "Не совсем понял, можете переформулировать? 😊"

                # Добавляем подсказку в зависимости от текущего состояния
                if context.current_state == FlowState.WORK_SCHEME:
                    fallback_text += "\nВарианты: под ключ • давальческое сырьё"
                elif context.current_state == FlowState.QUANTITY_COLORS:
                    if not context.colors_count:
                        fallback_text += "\nНапример: 3 цвета"
                    elif not context.total_quantity:
                        fallback_text += "\nНапример: 5000 штук"

                await self.send_telegram_message(chat_id, fallback_text, update_id)
                await self.save_context(context, update_id)

                return {
                    "status": "recovered",
                    "error": str(e),
                    "state": context.current_state.value,
                    "fallback_sent": True,
                }
            except Exception as recovery_error:
                # Последний рубеж - базовый fallback
                logger.error(f"Recovery also failed: {recovery_error}")
                basic_fallback = (
                    "Технический сбой. Начните, пожалуйста, заново с описания изделия."
                )
                await self.send_telegram_message(chat_id, basic_fallback, update_id)
                return {"status": "critical_error", "error": str(e)}

    async def process_callback_query(
        self, user_info: Dict[str, Any], callback_data: str, update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """ГОРЯЧИЙ ПАТЧ: Обработка callback_query для inline кнопок"""
        chat_id = user_info["chat_id"]
        callback_query_id = update.get("callback_query", {}).get("id")

        try:
            context = await self.get_context(chat_id)
            response_text = ""

            if callback_data == "change_quantity":
                context.total_quantity = None
                await self.transition_to_state(
                    context, FlowState.QUANTITY_COLORS, "callback_change"
                )
                response_text = "Укажите новое количество:"

            elif callback_data == "change_colors":
                context.colors_count = None
                await self.transition_to_state(
                    context, FlowState.QUANTITY_COLORS, "callback_change"
                )
                response_text = "Укажите новое количество цветов:"

            elif callback_data == "connect_manager":
                response_text = "Передаю заявку менеджеру. В течение часа с вами свяжется специалист."
                # TODO: интеграция с CRM для передачи заявки

            elif callback_data == "continue":
                await self.transition_to_state(
                    context, FlowState.FABRIC_DETAILS, "callback_continue"
                )
                response_text = "Продолжаем! Какая ткань нужна?"

            # Ответ на callback_query (убирает индикатор загрузки)
            if self.telegram_bot and callback_query_id:
                try:
                    await self.telegram_bot.answer_callback_query(callback_query_id)
                except Exception as e:
                    logger.warning(f"Failed to answer callback query: {e}")

            # Отправляем текстовый ответ
            if response_text:
                await self.send_telegram_message(
                    chat_id, response_text, update.get("update_id")
                )

            await self.save_context(context, update.get("update_id"))

            return {
                "callback_processed": True,
                "state": context.current_state.value,
                "response_sent": bool(response_text),
            }

        except Exception as e:
            logger.error(f"Callback query processing error: {e}")
            # Error guard: мягкий ответ пользователю
            if self.telegram_bot and callback_query_id:
                try:
                    await self.telegram_bot.answer_callback_query(
                        callback_query_id, text="Произошла ошибка, попробуйте ещё раз"
                    )
                except Exception:
                    pass
            return {"callback_processed": False, "error": str(e)}


# Singleton
_flow_manager_instance = None


async def get_flow_manager(redis_url: str = None) -> FlowManager:
    global _flow_manager_instance
    if _flow_manager_instance is None:
        _flow_manager_instance = FlowManager(redis_url)
        await _flow_manager_instance.initialize()
    return _flow_manager_instance
