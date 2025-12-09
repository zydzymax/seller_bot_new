"""
Two-Layer Flow Manager — Двухслойная архитектура диалога

Слой 1: Script Pre-Bot (без LLM) — сбор базовых данных
Слой 2: AI Seller (с LLM) — умная продажа

Экономит токены на шаблонных вопросах.
"""

import os
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

import redis.asyncio as redis
from telegram import Bot

from utils.logging import get_logger
from utils.conversation_logger import get_conversation_db, ConversationLog, LeadAttributes
from llm.orchestrator import get_orchestrator
from dialog.script_prebot import ScriptPreBot, PreBotContext, PreBotState, get_prebot
from dialog.intent_helpers import (
    is_agreement,
    is_rejection,
    extract_number,
    is_price_objection,
    is_timing_objection,
    is_competitor_objection,
    is_closing_signal,
    is_time_preference,
    is_contact_info,
    extract_time_preference,
    extract_contact,
    # Closing helpers
    is_dont_know_time,
    is_vague_time_period,
    has_specific_day_or_time,
    extract_vague_period
)
from llm.llm_orchestrator import generate_response as llm_generate_response, select_model
from config.tariffs_loader import (
    get_tariffs_config,
    get_min_price,
    recommend_tariff
)

logger = get_logger(__name__)

# Путь к системному промпту AI-продавца
AI_SELLER_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "config",
    "domains",
    "ai_seller_self",
    "prompts",
    "ai_seller_system_prompt.md"
)


@dataclass
class TwoLayerContext:
    """Контекст двухслойного диалога"""
    conversation_id: str
    chat_id: int
    user_id: int

    # Текущий слой: "prebot" или "ai_seller"
    current_layer: str = "prebot"

    # Контекст pre-bot
    prebot_state: str = "GREETING"
    prebot_message_count: int = 0

    # Собранные слоты (общие для обоих слоёв)
    slots: Dict[str, Any] = field(default_factory=dict)

    # История сообщений
    message_history: List[Dict[str, str]] = field(default_factory=list)

    # Метаданные
    started_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)
    total_message_count: int = 0

    # AI Seller mode tracking
    has_main_pitch: bool = False  # Уже был главный питч?
    last_ai_mode: str = ""  # Последний использованный режим

    # Стадия диалога:
    # "prebot" - сбор слотов
    # "ai_seller" - основная продажа
    # "closing_wait_time" - ждём выбор времени для созвона
    # "closing_wait_contact" - ждём контакт клиента
    # "closed" - диалог завершён
    conversation_stage: str = "prebot"

    # Данные закрытия
    preferred_period: str = ""  # расплывчатый период ("следующая неделя", "начало недели")
    preferred_time: str = ""  # конкретное время ("днём", "в понедельник 15:00")
    client_contact: str = ""  # контакт клиента
    closing_attempts: int = 0  # счётчик вопросов про время (макс 2)

    # Флаги для предотвращения зацикливания
    small_leads_mentioned: bool = False  # Уже сказали про малый объём заявок?
    price_objection_count: int = 0  # Счётчик возражений по цене

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "current_layer": self.current_layer,
            "prebot_state": self.prebot_state,
            "prebot_message_count": self.prebot_message_count,
            "slots": self.slots,
            "message_history": self.message_history[-15:],
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
            "total_message_count": self.total_message_count,
            "has_main_pitch": self.has_main_pitch,
            "last_ai_mode": self.last_ai_mode,
            "conversation_stage": self.conversation_stage,
            "preferred_period": self.preferred_period,
            "preferred_time": self.preferred_time,
            "client_contact": self.client_contact,
            "closing_attempts": self.closing_attempts,
            "small_leads_mentioned": self.small_leads_mentioned,
            "price_objection_count": self.price_objection_count
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TwoLayerContext":
        return cls(
            conversation_id=data["conversation_id"],
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            current_layer=data.get("current_layer", "prebot"),
            prebot_state=data.get("prebot_state", "GREETING"),
            prebot_message_count=data.get("prebot_message_count", 0),
            slots=data.get("slots", {}),
            message_history=data.get("message_history", []),
            started_at=data.get("started_at", time.time()),
            last_message_at=data.get("last_message_at", time.time()),
            total_message_count=data.get("total_message_count", 0),
            has_main_pitch=data.get("has_main_pitch", False),
            last_ai_mode=data.get("last_ai_mode", ""),
            conversation_stage=data.get("conversation_stage", "prebot"),
            preferred_period=data.get("preferred_period", ""),
            preferred_time=data.get("preferred_time", ""),
            client_contact=data.get("client_contact", ""),
            closing_attempts=data.get("closing_attempts", 0),
            small_leads_mentioned=data.get("small_leads_mentioned", False),
            price_objection_count=data.get("price_objection_count", 0)
        )


class TwoLayerFlowManager:
    """
    Двухслойный Flow Manager

    Слой 1: Pre-Bot (без LLM) — собирает базовые слоты
    Слой 2: AI Seller (LLM) — ведёт умную продажу
    """

    def __init__(self):
        # Redis для хранения контекста
        redis_url = os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
        self.redis_client = redis.from_url(redis_url, decode_responses=False)

        # Pre-bot (скриптовый)
        self.prebot = get_prebot()

        # LLM orchestrator (lazy init)
        self._llm = None

        # Системный промпт AI-продавца
        self._ai_seller_prompt = None

        # Conversation logger
        self.conv_db = get_conversation_db()

        # Telegram bot
        token = os.getenv("TELEGRAM_TOKEN")
        self.bot = Bot(token=token) if token else None

        # Tariffs config
        self.tariffs = get_tariffs_config()

        logger.info("TwoLayerFlowManager initialized")

    async def get_llm(self):
        """Lazy init LLM orchestrator"""
        if self._llm is None:
            self._llm = await get_orchestrator()
        return self._llm

    def get_ai_seller_prompt(self) -> str:
        """Загрузить системный промпт AI-продавца"""
        if self._ai_seller_prompt is None:
            try:
                with open(AI_SELLER_PROMPT_PATH, "r", encoding="utf-8") as f:
                    self._ai_seller_prompt = f.read()
            except Exception as e:
                logger.error(f"Failed to load AI seller prompt: {e}")
                self._ai_seller_prompt = "Ты — AI-продавец SalesWhisper."

        return self._ai_seller_prompt

    async def get_context(self, chat_id: int) -> TwoLayerContext:
        """Получить или создать контекст"""
        key = f"twolayer:context:{chat_id}"

        try:
            cached = await self.redis_client.get(key)
            if cached:
                data = json.loads(cached.decode("utf-8"))
                return TwoLayerContext.from_dict(data)
        except Exception as e:
            logger.error(f"Error loading context: {e}")

        # Новый контекст
        return TwoLayerContext(
            conversation_id=f"conv_{chat_id}_{int(time.time())}",
            chat_id=chat_id,
            user_id=chat_id
        )

    async def save_context(self, context: TwoLayerContext):
        """Сохранить контекст в Redis"""
        key = f"twolayer:context:{context.chat_id}"
        ttl = int(os.getenv("SESSION_TTL_SEC", "3600"))

        try:
            data = json.dumps(context.to_dict(), default=str)
            await self.redis_client.setex(key, ttl, data)
        except Exception as e:
            logger.error(f"Error saving context: {e}")

    async def process_message(
        self,
        user_info: Dict[str, Any],
        message_text: str,
        message_data: Dict[str, Any],
        update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обработать входящее сообщение.

        Определяет слой и передаёт обработку:
        - prebot: скриптовый сбор данных
        - ai_seller: LLM-продажа
        """
        chat_id = user_info.get("chat_id")
        update_id = update.get("update_id")

        try:
            # Загружаем контекст
            context = await self.get_context(chat_id)
            context.user_id = user_info.get("user_id", chat_id)
            context.total_message_count += 1
            context.last_message_at = time.time()

            # Сохраняем сообщение пользователя
            context.message_history.append({
                "role": "user",
                "content": message_text
            })

            # Log message
            self.conv_db.save_message(
                conversation_id=context.conversation_id,
                role="user",
                content=message_text
            )

            # Определяем слой и обрабатываем на основе conversation_stage
            stage = context.conversation_stage

            if stage == "closed":
                # Диалог завершён — вежливый ответ без новой продажи
                response = await self._process_closed_stage(message_text, context)
            elif stage in ("closing_wait_time", "closing_wait_contact"):
                # Стадия закрытия — обрабатываем время/контакт
                response = await self._process_closing_stage(message_text, context)
            elif context.current_layer == "prebot" and stage == "prebot":
                response = await self._process_prebot(message_text, context)
            else:
                # AI Seller работает
                response = await self._process_ai_seller(message_text, context)

            # Сохраняем ответ бота
            if response:
                context.message_history.append({
                    "role": "assistant",
                    "content": response
                })

                self.conv_db.save_message(
                    conversation_id=context.conversation_id,
                    role="assistant",
                    content=response
                )

                # Отправляем в Telegram
                await self._send_telegram_message(chat_id, response, update_id)

            # Сохраняем контекст
            await self.save_context(context)

            return {
                "status": "ok",
                "layer": context.current_layer,
                "response_sent": True,
                "conversation_id": context.conversation_id
            }

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

            error_msg = "Извините, произошла ошибка. Попробуйте ещё раз."
            try:
                await self._send_telegram_message(chat_id, error_msg, update_id)
            except:
                pass

            return {
                "status": "error",
                "error": str(e),
                "response_sent": False
            }

    async def _process_prebot(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """Обработка скриптовым pre-bot"""
        logger.info(f"PreBot layer: state={context.prebot_state}")

        # Создаём PreBotContext из TwoLayerContext
        prebot_ctx = PreBotContext(
            state=PreBotState(context.prebot_state),
            slots=context.slots.copy(),
            message_count=context.prebot_message_count
        )

        # Обрабатываем сообщение
        response, updated_ctx, handoff = self.prebot.process_message(message, prebot_ctx)

        # Обновляем TwoLayerContext
        context.prebot_state = updated_ctx.state.value
        context.prebot_message_count = updated_ctx.message_count
        context.slots.update(updated_ctx.slots)

        # Проверяем handoff в AI
        if handoff:
            logger.info("Handoff to AI Seller")
            context.current_layer = "ai_seller"

            # Генерируем первое сообщение AI-продавца
            response = await self._generate_ai_seller_intro(context)

        return response

    async def _process_ai_seller(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """Обработка AI-продавцом (LLM) с режимами"""
        logger.info(f"AI Seller layer, stage={context.conversation_stage}")

        # Извлекаем бюджет если есть
        self._extract_budget(message, context)

        # --- ВАЖНО: Проверяем контактные данные в любой момент ---
        # Если клиент присылает контакт (@username, телефон) — закрываем диалог
        contact = extract_contact(message)
        if contact or is_contact_info(message):
            context.client_contact = contact or message.strip()
            context.conversation_stage = "closed"
            logger.info(f"Contact detected in ai_seller: {context.client_contact}, stage -> closed")

            # Формируем финальный ответ
            time_info = ""
            if context.preferred_period:
                time_info = f" ({context.preferred_period})"
            elif context.preferred_time:
                time_info = f" ({context.preferred_time})"

            return f"Принял контакт, спасибо!\n\nПередам менеджеру, он свяжется{time_info}, чтобы обсудить детали."

        # Определяем режим
        mode = self._detect_ai_mode(message, context)
        logger.info(f"AI Seller mode detected: {mode}, price_objection_count={context.price_objection_count}")

        # --- Обновляем счётчики и флаги ДО генерации ---

        # Счётчик возражений по цене
        if mode == "price_objection":
            context.price_objection_count += 1
            logger.info(f"Price objection #{context.price_objection_count}")
        elif mode == "closing":
            # Если клиент готов к closing — сбрасываем счётчик возражений
            context.price_objection_count = 0

        # Флаг "уже говорили про малый объём"
        if mode == "not_fit_small_leads":
            context.small_leads_mentioned = True
            logger.info("Small leads mentioned — flag set")

        # Генерируем ответ через оркестратор
        response = await self._generate_ai_response_with_mode(message, context, mode)

        # --- Обновляем контекст ПОСЛЕ ответа ---

        if mode == "main_pitch":
            context.has_main_pitch = True
            context.conversation_stage = "ai_seller"

        # Режим closing — сразу переходим на стадию ожидания времени
        # (не зависим от парсинга текста ответа)
        if mode == "closing":
            context.conversation_stage = "closing_wait_time"
            logger.info("Stage -> closing_wait_time (mode=closing)")

        context.last_ai_mode = mode

        return response

    async def _process_closing_stage(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """
        Обработка стадии закрытия (время/контакт).

        Ключевые правила:
        - Макс 2 открытых вопроса про время (closing_attempts)
        - Если "не знаю" + attempts >= 2 → предлагаем оставить контакт или мягко сворачиваем
        - Если конкретный день/время → сразу к контакту
        - Если расплывчатый период → уточняем 1 раз с альтернативами
        """
        stage = context.conversation_stage
        logger.info(f"Closing stage: {stage}, closing_attempts={context.closing_attempts}")

        # --- Проверяем возражения — возвращаемся к AI Seller ---
        if is_price_objection(message):
            context.conversation_stage = "ai_seller"
            return await self._process_ai_seller(message, context)

        if is_timing_objection(message):
            context.conversation_stage = "ai_seller"
            return await self._process_ai_seller(message, context)

        if is_rejection(message):
            context.conversation_stage = "closed"
            return "Понял, без проблем. Если передумаете — напишите, буду рад помочь."

        # === STAGE: closing_wait_time ===
        if stage == "closing_wait_time":
            return await self._handle_closing_wait_time(message, context)

        # === STAGE: closing_wait_contact ===
        elif stage == "closing_wait_contact":
            return await self._handle_closing_wait_contact(message, context)

        # Fallback
        return await self._process_ai_seller(message, context)

    async def _handle_closing_wait_time(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """Обработка ожидания выбора времени."""

        # 1. Проверяем конкретный день/время
        if has_specific_day_or_time(message):
            context.preferred_time = message.strip()
            context.conversation_stage = "closing_wait_contact"
            context.closing_attempts = 0  # сбрасываем
            logger.info(f"Specific time: {context.preferred_time}, stage -> closing_wait_contact")

            return f"Отлично, записал: {context.preferred_time}.\n\nОставьте, пожалуйста, контакт (Telegram/WhatsApp или телефон), куда удобнее написать — передам менеджеру."

        # 2. Проверяем расплывчатый период ("на следующей неделе")
        vague_period = extract_vague_period(message)
        if vague_period or is_vague_time_period(message):
            context.preferred_period = vague_period or message.strip()
            context.closing_attempts += 1
            logger.info(f"Vague period: {context.preferred_period}, attempts={context.closing_attempts}")

            # Если это первый вопрос — предлагаем альтернативы
            if context.closing_attempts <= 1:
                return f"Хорошо, {context.preferred_period}. Удобнее в начале недели (пн–вт) или ближе к концу (чт–пт)? И в первой половине дня или после обеда?"

            # Второй+ вопрос — сразу к контакту
            context.conversation_stage = "closing_wait_contact"
            return f"Ок, ориентируемся на {context.preferred_period}.\n\nОставьте контакт (Telegram/WhatsApp или телефон), и менеджер предложит конкретное время."

        # 3. Проверяем is_time_preference (днём, утром, вечером)
        time_pref = extract_time_preference(message)
        if time_pref or is_time_preference(message):
            context.preferred_time = time_pref or message.strip()
            context.conversation_stage = "closing_wait_contact"
            context.closing_attempts = 0
            logger.info(f"Time preference: {context.preferred_time}, stage -> closing_wait_contact")

            period_info = f" {context.preferred_period}" if context.preferred_period else ""
            return f"Отлично,{period_info} {context.preferred_time}.\n\nОставьте контакт (Telegram/WhatsApp или телефон), куда удобнее написать."

        # 4. Проверяем "не знаю" / "сложно сказать"
        if is_dont_know_time(message):
            context.closing_attempts += 1
            logger.info(f"Dont know time, attempts={context.closing_attempts}")

            # Если >= 2 попыток — не мучаем, предлагаем оставить контакт
            if context.closing_attempts >= 2:
                context.conversation_stage = "closing_wait_contact"
                return "Давайте сделаем проще: оставьте контакт (Telegram/WhatsApp или телефон), и я на следующей неделе предложу пару вариантов по времени, а вы выберете."

            # Первая попытка — предлагаем альтернативы
            return "Понимаю. Давайте так: удобнее в первой половине дня или после обеда? Или могу сам написать на следующей неделе с парой вариантов."

        # 5. Любой другой ответ — инкрементируем и проверяем лимит
        context.closing_attempts += 1

        if context.closing_attempts >= 2:
            # Достигли лимита — мягко предлагаем оставить контакт
            context.conversation_stage = "closing_wait_contact"
            return "Давайте так: оставьте удобный контакт, и менеджер сам напишет с парой вариантов времени."

        # Ещё можем спросить
        return "Когда вам удобнее: в будни днём или ближе к вечеру?"

    async def _handle_closing_wait_contact(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """Обработка ожидания контакта."""
        contact = extract_contact(message)

        if contact or is_contact_info(message):
            context.client_contact = contact or message.strip()
            context.conversation_stage = "closed"
            logger.info(f"Contact received: {context.client_contact}, stage -> closed")

            # Формируем подтверждение с учётом собранной информации
            time_parts = []
            if context.preferred_period:
                time_parts.append(context.preferred_period)
            if context.preferred_time:
                time_parts.append(context.preferred_time)

            time_info = f" ({', '.join(time_parts)})" if time_parts else ""
            return f"Принял, спасибо!\n\nПередам контакт менеджеру, он свяжется{time_info}, чтобы согласовать детали."

        # Если "не знаю" или мягкий отказ на этапе контакта
        if is_dont_know_time(message) or is_rejection(message):
            context.conversation_stage = "closed"
            return "Окей, давайте ничего навязывать не буду. Если позже вернётесь к теме автоматизации заявок — можете просто написать сюда, буду на связи."

        # Напоминаем про контакт
        return "Оставьте, пожалуйста, контакт (Telegram, WhatsApp или телефон), чтобы менеджер мог связаться."

    async def _process_closed_stage(
        self,
        message: str,
        context: TwoLayerContext
    ) -> str:
        """Обработка после закрытия диалога"""
        logger.info("Closed stage — polite response only")

        # Если клиент что-то спрашивает после закрытия
        if "?" in message:
            return "Менеджер скоро свяжется с вами и ответит на все вопросы. Если что-то срочное — напишите прямо сюда."

        # Благодарность за повторное сообщение
        return "Спасибо! Менеджер уже получил ваш контакт и скоро свяжется."

    def _detect_ai_mode(self, message: str, context: TwoLayerContext) -> str:
        """
        Определить режим AI-продавца.

        Приоритет (важен!):
            1. main_pitch - первый главный ответ (ещё не было питча)
            2. closing - готовность к следующему шагу (ВЫШЕ возражений!)
            3. price_objection - возражение по цене
            4. timing_objection - не сейчас, позже
            5. competitor_objection - у нас уже есть бот/подрядчик
            6. not_fit_small_leads - мало заявок (только 1 раз!)
            7. followup - всё остальное
        """
        # 1. Если ещё не было главного питча
        if not context.has_main_pitch:
            logger.debug("Mode: main_pitch (no pitch yet)")
            return "main_pitch"

        # 2. СНАЧАЛА проверяем closing (до возражений!)
        # Это важно: "давайте созвонимся" должно быть closing, даже если есть слово "дорого"
        if is_closing_signal(message):
            logger.debug("Mode: closing (ready for next step)")
            return "closing"

        # 3. Возражения по приоритету
        if is_price_objection(message):
            logger.debug(f"Mode: price_objection (count={context.price_objection_count})")
            return "price_objection"

        if is_timing_objection(message):
            logger.debug("Mode: timing_objection")
            return "timing_objection"

        if is_competitor_objection(message):
            logger.debug("Mode: competitor_objection")
            return "competitor_objection"

        # 4. Малый объём заявок — только если ещё НЕ ГОВОРИЛИ об этом
        leads = context.slots.get("leads_per_month", 0)
        if isinstance(leads, str):
            try:
                leads = int(leads)
            except ValueError:
                leads = 0

        if leads > 0 and leads < 50:
            if not context.small_leads_mentioned:
                logger.debug(f"Mode: not_fit_small_leads (leads={leads})")
                return "not_fit_small_leads"

        # 5. Все остальные случаи — followup
        logger.debug("Mode: followup (default)")
        return "followup"

    def _extract_budget(self, message: str, context: TwoLayerContext):
        """Извлечь бюджет из сообщения"""
        message_lower = message.lower()

        # Признаки бюджета
        budget_indicators = ["руб", "₽", "р.", "тыс", "к ", "бюджет"]
        has_budget_indicator = any(ind in message_lower for ind in budget_indicators)

        if has_budget_indicator:
            number = extract_number(message)
            if number:
                # Корректируем если указано в тысячах
                if number < 1000 and ("тыс" in message_lower or "к" in message_lower):
                    number *= 1000
                context.slots["budget_user"] = number
                logger.info(f"Extracted budget: {number}")

    async def _generate_ai_seller_intro(self, context: TwoLayerContext) -> str:
        """Генерировать вступительное сообщение AI-продавца (main_pitch)"""
        # Формируем контекст для LLM
        slots_summary = self._format_slots_for_prompt(context.slots)

        intro_prompt = f"""[MODE: main_pitch]

Клиент только что завершил анкету. Вот собранные данные:

{slots_summary}

Теперь твоя задача — КРАТКО отразить ситуацию клиента (2-3 предложения), показать ценность ИИ-бота под их нишу, и дать ориентир по тарифам.

НЕ СПРАШИВАЙ информацию, которая уже есть в данных выше!
Сразу переходи к содержательной части."""

        # Используем новый оркестратор
        mode = "main_pitch"
        model = select_model(mode, context.slots)
        logger.info(f"AI Seller intro: mode={mode}, model={model}")

        response = await self._call_llm_with_mode(intro_prompt, context, mode)

        # Обновляем статус
        context.has_main_pitch = True
        context.last_ai_mode = mode
        context.conversation_stage = "ai_seller"  # Важно: переводим стадию!
        logger.info("Stage -> ai_seller (after main_pitch)")

        return response

    async def _generate_ai_response_with_mode(
        self,
        message: str,
        context: TwoLayerContext,
        mode: str
    ) -> str:
        """Генерировать ответ AI-продавца с учётом режима"""
        # Проверяем бюджет
        budget = context.slots.get("budget_user")
        min_price = self.tariffs.min_price

        # Специальная обработка низкого бюджета
        budget_warning = ""
        if budget and budget < min_price:
            budget_warning = f"""
⚠️ ВАЖНО: Клиент назвал бюджет {budget} ₽, что НИЖЕ минимальной стоимости {min_price} ₽.
Объясни ценность, но честно скажи что это ниже входного порога.
НЕ ПРИДУМЫВАЙ дешёвые тарифы!
"""

        # Формируем user prompt с учётом режима
        slots_summary = self._format_slots_for_prompt(context.slots)

        # Добавляем указание режима в промпт
        mode_instruction = ""
        if mode == "price_objection":
            objection_count = context.price_objection_count
            if objection_count == 1:
                # Первое возражение — полная отработка
                mode_instruction = """[MODE: price_objection] (первое возражение)
Клиент выразил возражение по цене (дорого, нет бюджета).
НЕ повторяй полностью главный питч.
Признай что сумма ощутимая, покажи экономику потерь на примере их объёма заявок.
Если бюджет клиента ниже 25к — честно скажи что дешевле не делаем.
Предложи конкретный следующий шаг (созвон/контакт).
"""
            elif objection_count == 2:
                # Второе возражение — короткий ответ
                mode_instruction = """[MODE: price_objection] (повторное возражение)
Клиент ПОВТОРНО говорит про цену.
НЕ повторяй ту же лекцию.
Коротко (2-3 предложения): "Понимаю, это инвестиция. Если сейчас не готовы — без проблем, можно вернуться позже."
Оставь дверь открытой, но не дави.
"""
            else:
                # Третье+ возражение — вежливое завершение
                mode_instruction = """[MODE: price_objection] (многократное возражение)
Клиент уже несколько раз говорил про цену.
Вежливо заверши: "Понял, сейчас это не в приоритете. Если ситуация изменится — пишите, буду рад помочь."
НЕ продолжай продавать.
"""
        elif mode == "timing_objection":
            mode_instruction = """[MODE: timing_objection]
Клиент говорит "не сейчас", "позже", "нет времени".
Признай что время имеет значение.
Мягко покажи что автоматизация помогает переживать пики.
Предложи мягкий next step: короткий созвон или честно зафиксировать "вернёмся позже".
"""
        elif mode == "competitor_objection":
            mode_instruction = """[MODE: competitor_objection]
Клиент говорит "у нас уже есть бот/подрядчик".
НЕ хейти конкурентов.
Подчеркни что можно усилить существующую связку или провести аудит позже.
Если явного интереса нет — аккуратно закончи.
"""
        elif mode == "not_fit_small_leads":
            mode_instruction = """[MODE: not_fit_small_leads]
У клиента менее 50 заявок в месяц.
Честно скажи что при таком объёме ИИ-продавец может не окупиться.
НЕ продавай во что бы то ни стало.
Оставь дверь открытой: "Если объём вырастет, можно вернуться."
"""
        elif mode == "closing":
            mode_instruction = """[MODE: closing]
Клиент готов к следующему шагу (созвон, обсуждение).

КРИТИЧЕСКИ ВАЖНО:
1. НЕ ЗДОРОВАЙСЯ заново — никаких "Привет", "Добрый день"
2. НЕ повторяй презентацию и тарифы
3. Задай ОДИН конкретный вопрос: "Когда удобнее: днём или вечером?"
4. После получения времени — попроси контакт
5. После получения контакта — подтверди и заверши
"""
        elif mode == "main_pitch":
            mode_instruction = "[MODE: main_pitch]\n"
        else:
            mode_instruction = """[MODE: followup]
Отвечай по существу вопроса клиента.
НЕ начинай заново презентацию.
Коротко, по делу. Один вопрос за раз.
"""

        user_prompt = f"""{mode_instruction}
Данные о клиенте:
{slots_summary}

{budget_warning}

Сообщение клиента: "{message}"

Ответь как AI-продавец SalesWhisper. Один вопрос за раз. Не повторяй вопросы."""

        model = select_model(mode, context.slots)
        logger.info(f"AI Seller response: mode={mode}, model={model}")

        return await self._call_llm_with_mode(user_prompt, context, mode)

    async def _call_llm_with_mode(
        self,
        user_prompt: str,
        context: TwoLayerContext,
        mode: str
    ) -> str:
        """Вызвать LLM через новый оркестратор с режимом"""
        try:
            system_prompt = self.get_ai_seller_prompt()

            # Формируем сообщения для оркестратора
            messages = []
            for msg in context.message_history[-10:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

            # Добавляем текущий промпт
            messages.append({"role": "user", "content": user_prompt})

            # Формируем контекст для выбора модели
            llm_context = {
                "chat_id": context.chat_id,
                "leads_per_month": context.slots.get("leads_per_month"),
                "decision_maker": context.slots.get("decision_maker"),
                "niche": context.slots.get("niche"),
                "has_main_pitch": context.has_main_pitch
            }

            # Вызываем новый оркестратор
            response = await llm_generate_response(
                mode=mode,
                context=llm_context,
                messages=messages,
                system_prompt=system_prompt
            )

            return response

        except Exception as e:
            logger.error(f"LLM error: {e}", exc_info=True)
            return "Извините, возникла техническая ошибка. Можете повторить?"

    async def _call_llm(self, user_prompt: str, context: TwoLayerContext) -> str:
        """Вызвать LLM (legacy, для совместимости)"""
        return await self._call_llm_with_mode(user_prompt, context, "generic")

    def _format_slots_for_prompt(self, slots: Dict[str, Any]) -> str:
        """Форматировать слоты для промпта"""
        slot_names = {
            "name": "Имя",
            "niche": "Ниша",
            "leads_per_month": "Заявок в месяц",
            "channels": "Каналы",
            "current_process": "Текущий процесс",
            "pain": "Боль",
            "decision_maker": "ЛПР",
            "budget_user": "Бюджет клиента"
        }

        lines = []
        for key, label in slot_names.items():
            if key in slots and slots[key]:
                value = slots[key]
                if key == "decision_maker":
                    value = {"self": "сам клиент", "partner": "партнёр", "owner": "руководитель"}.get(value, value)
                lines.append(f"• {label}: {value}")

        return "\n".join(lines) if lines else "Нет данных"

    async def _send_telegram_message(
        self,
        chat_id: int,
        text: str,
        update_id: Optional[int] = None
    ):
        """Отправить сообщение в Telegram"""
        if not self.bot:
            logger.warning("Telegram bot not initialized")
            return

        try:
            await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
            logger.info(f"Message sent to {chat_id}", update_id=update_id)
        except Exception as e:
            logger.error(f"Error sending message: {e}")


# Глобальный инстанс
_two_layer_manager: Optional[TwoLayerFlowManager] = None


def get_two_layer_flow_manager() -> TwoLayerFlowManager:
    """Получить TwoLayerFlowManager"""
    global _two_layer_manager
    if _two_layer_manager is None:
        _two_layer_manager = TwoLayerFlowManager()
    return _two_layer_manager
