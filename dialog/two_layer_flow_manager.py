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
from utils.conversation_logger import (
    get_conversation_db,
)
from llm.orchestrator import get_orchestrator
from dialog.script_prebot import PreBotContext, PreBotState, get_prebot
from dialog.intent_helpers import (
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
    extract_vague_period,
    # Soft opt-out helpers
    is_not_now,
    looks_like_noise,
    # Price question detection
    is_price_question,
    # Positive confirmation detection
    is_positive_confirmation,
)
from llm.llm_orchestrator import (
    generate_response as llm_generate_response,
    select_model,
)
from config.tariffs_loader import get_tariffs_config
from dialog.fsm_router import (
    decide_next_stage,
    get_diag_question,
    get_stage_model,
    # Anti-interrogation
    is_clarification_question,
    get_simplified_prompt_instruction,
    get_force_move_prompt,
)

logger = get_logger(__name__)


# ============== СРЕДНИЙ ЧЕК ПО НИШЕ ==============


def parse_avg_check(slots: Dict[str, Any]) -> Optional[int]:
    """
    Парсит средний чек из слотов.

    Returns:
        int или None
    """
    raw = slots.get("avg_check")
    if not raw:
        return None
    if isinstance(raw, int):
        return raw
    import re

    m = re.search(r"\d[\d\s]*", str(raw))
    if not m:
        return None
    try:
        return int(m.group(0).replace(" ", ""))
    except ValueError:
        return None


def estimate_avg_check_from_niche(slots: Dict[str, Any]) -> Optional[int]:
    """
    Оценивает средний чек на основе ниши клиента.

    Returns:
        Примерный средний чек или None
    """
    niche = (slots.get("niche") or "").lower()

    # Швейная фабрика / опт / производство — высокий чек B2B
    if any(
        k in niche
        for k in ["швейн", "пошив", "фабрик", "производство", "опт", "текстил"]
    ):
        return 80000  # порядок 50–150k

    # Стройматериалы / оптовые поставки
    if any(
        k in niche
        for k in [
            "строймат",
            "строител",
            "склад",
            "оптовые поставки",
            "оптовая продажа",
            "стройк",
        ]
    ):
        return 60000  # порядок 30–100k

    # Онлайн-школы / эксперты / инфобизнес
    if any(
        k in niche
        for k in [
            "онлайн-школ",
            "курс",
            "обучен",
            "инфобизн",
            "ментор",
            "коуч",
            "тренинг",
        ]
    ):
        return 20000  # порядок 10–30k

    # Стоматология / медицина
    if any(
        k in niche
        for k in ["стоматолог", "клиник", "медицин", "имплант", "ортодонт", "стомат"]
    ):
        return 8000  # порядок 5–15k

    # Бьюти / массаж / салоны — низкий чек
    if any(
        k in niche
        for k in [
            "бьюти",
            "салон",
            "маникюр",
            "косметолог",
            "массаж",
            "парикмах",
            "визаж",
        ]
    ):
        return 4000  # порядок 2–6k

    # Недвижимость — высокий чек
    if any(
        k in niche
        for k in ["недвиж", "квартир", "риелтор", "агентство недв", "застройщ"]
    ):
        return 150000  # комиссия от сделки

    # Авто — средний/высокий чек
    if any(
        k in niche for k in ["авто", "машин", "автосал", "автосерв", "сто ", "шиномонт"]
    ):
        return 15000  # порядок 5–30k

    # Юридические услуги
    if any(k in niche for k in ["юрист", "юридич", "адвокат", "нотариус", "правов"]):
        return 25000  # порядок 10–50k

    # IT / разработка / digital
    if any(
        k in niche
        for k in [
            "it ",
            "разработ",
            "сайт",
            "приложен",
            "digital",
            "маркетинг",
            "реклам",
        ]
    ):
        return 50000  # порядок 30–100k

    # Ремонт / строительство услуги
    if any(k in niche for k in ["ремонт", "отделк", "сантехн", "электрик"]):
        return 30000  # порядок 10–100k

    return None


# Путь к системному промпту AI-продавца
AI_SELLER_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "config",
    "domains",
    "ai_seller_self",
    "prompts",
    "ai_seller_system_prompt.md",
)


@dataclass
class TwoLayerContext:
    """
    Контекст двухслойного диалога с FSM-стадиями.

    FSM СТАДИИ:
    - PREBOT: сбор базовых слотов (без LLM)
    - DIAG_Q: диагностические вопросы (без LLM, 2-3 вопроса)
    - NEED_SUMMARY: LLM формирует резюме потребности
    - VALUE_PITCH: LLM продаёт выгоду (без цены!)
    - COMMIT_TEST: LLM проверяет интерес
    - PRICE_DISCUSSION: только если клиент спросил цену
    - OBJECTION_HANDLING: обработка возражений
    - CLOSING: закрытие на созвон/контакт
    - NURTURE: мягкое сопровождение
    - CLOSED: диалог завершён
    """

    conversation_id: str
    chat_id: int
    user_id: int

    # ===== FSM СТАДИЯ =====
    # Это ГЛАВНОЕ поле, определяющее поведение бота
    stage: str = "PREBOT"

    # Подтип для OBJECTION_HANDLING: price, timing, competitor, think_about
    objection_type: str = ""

    # ===== PREBOT =====
    prebot_state: str = "GREETING"
    prebot_message_count: int = 0

    # ===== DIAG_Q =====
    diag_question_index: int = 0  # какой вопрос сейчас (0, 1, 2)
    diag_answers: Dict[str, str] = field(default_factory=dict)  # ответы на DIAG_Q

    # ===== СЛОТЫ =====
    slots: Dict[str, Any] = field(default_factory=dict)

    # ===== ИСТОРИЯ =====
    message_history: List[Dict[str, str]] = field(default_factory=list)

    # ===== МЕТАДАННЫЕ =====
    started_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)
    total_message_count: int = 0

    # ===== ФЛАГИ ПРОГРЕССА =====
    problem_clarified: bool = False  # боль уточнена через DIAG_Q
    has_need_summary: bool = False  # резюме потребности сделано
    need_summary_text: str = ""  # текст резюме
    value_pitch_done: bool = False  # VALUE_PITCH выполнен
    commit_test_done: bool = False  # COMMIT_TEST выполнен

    # ===== ЦЕНА =====
    user_asked_price_recently: bool = False  # клиент спросил про цену
    price_discussed: bool = False  # цена уже обсуждалась

    # ===== ВОЗРАЖЕНИЯ =====
    price_objection_count: int = 0  # сколько раз "дорого"
    roi_explained: bool = False  # экономика уже объяснена
    small_leads_mentioned: bool = False  # сказали про малый объём

    # ===== CLOSING =====
    closing_attempts: int = 0  # попытки закрыть на созвон (макс 2)
    preferred_time: str = ""  # когда созвон
    client_contact: str = ""  # контакт клиента
    contact_captured: bool = False  # контакт получен

    # ===== SOFT OPT-OUT / NURTURE =====
    soft_opt_out: bool = False  # клиент сказал "не сейчас"
    nurture_offers_made: int = 0  # предложений чек-листа

    # ===== ANTI-INTERROGATION (защита от допроса) =====
    clarification_attempts: int = 0  # счётчик уточняющих вопросов подряд (макс 2)
    last_clarification_topic: str = ""  # тема, по которой уточняем

    # ===== LEGACY (для совместимости) =====
    current_layer: str = "prebot"
    conversation_stage: str = "prebot"
    has_main_pitch: bool = False
    last_ai_mode: str = ""
    preferred_period: str = ""
    followup_count: int = 0

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            # FSM
            "stage": self.stage,
            "objection_type": self.objection_type,
            # PREBOT
            "prebot_state": self.prebot_state,
            "prebot_message_count": self.prebot_message_count,
            # DIAG_Q
            "diag_question_index": self.diag_question_index,
            "diag_answers": self.diag_answers,
            # SLOTS
            "slots": self.slots,
            # HISTORY
            "message_history": self.message_history[-15:],
            # META
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
            "total_message_count": self.total_message_count,
            # PROGRESS FLAGS
            "problem_clarified": self.problem_clarified,
            "has_need_summary": self.has_need_summary,
            "need_summary_text": self.need_summary_text,
            "value_pitch_done": self.value_pitch_done,
            "commit_test_done": self.commit_test_done,
            # PRICE
            "user_asked_price_recently": self.user_asked_price_recently,
            "price_discussed": self.price_discussed,
            # OBJECTIONS
            "price_objection_count": self.price_objection_count,
            "roi_explained": self.roi_explained,
            "small_leads_mentioned": self.small_leads_mentioned,
            # CLOSING
            "closing_attempts": self.closing_attempts,
            "preferred_time": self.preferred_time,
            "client_contact": self.client_contact,
            "contact_captured": self.contact_captured,
            # SOFT OPT-OUT
            "soft_opt_out": self.soft_opt_out,
            "nurture_offers_made": self.nurture_offers_made,
            # ANTI-INTERROGATION
            "clarification_attempts": self.clarification_attempts,
            "last_clarification_topic": self.last_clarification_topic,
            # LEGACY
            "current_layer": self.current_layer,
            "conversation_stage": self.conversation_stage,
            "has_main_pitch": self.has_main_pitch,
            "last_ai_mode": self.last_ai_mode,
            "preferred_period": self.preferred_period,
            "followup_count": self.followup_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TwoLayerContext":
        return cls(
            conversation_id=data["conversation_id"],
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            # FSM
            stage=data.get("stage", "PREBOT"),
            objection_type=data.get("objection_type", ""),
            # PREBOT
            prebot_state=data.get("prebot_state", "GREETING"),
            prebot_message_count=data.get("prebot_message_count", 0),
            # DIAG_Q
            diag_question_index=data.get("diag_question_index", 0),
            diag_answers=data.get("diag_answers", {}),
            # SLOTS
            slots=data.get("slots", {}),
            # HISTORY
            message_history=data.get("message_history", []),
            # META
            started_at=data.get("started_at", time.time()),
            last_message_at=data.get("last_message_at", time.time()),
            total_message_count=data.get("total_message_count", 0),
            # PROGRESS FLAGS
            problem_clarified=data.get("problem_clarified", False),
            has_need_summary=data.get("has_need_summary", False),
            need_summary_text=data.get("need_summary_text", ""),
            value_pitch_done=data.get("value_pitch_done", False),
            commit_test_done=data.get("commit_test_done", False),
            # PRICE
            user_asked_price_recently=data.get("user_asked_price_recently", False),
            price_discussed=data.get("price_discussed", False),
            # OBJECTIONS
            price_objection_count=data.get("price_objection_count", 0),
            roi_explained=data.get("roi_explained", False),
            small_leads_mentioned=data.get("small_leads_mentioned", False),
            # CLOSING
            closing_attempts=data.get("closing_attempts", 0),
            preferred_time=data.get("preferred_time", ""),
            client_contact=data.get("client_contact", ""),
            contact_captured=data.get("contact_captured", False),
            # SOFT OPT-OUT
            soft_opt_out=data.get("soft_opt_out", False),
            nurture_offers_made=data.get("nurture_offers_made", 0),
            # ANTI-INTERROGATION
            clarification_attempts=data.get("clarification_attempts", 0),
            last_clarification_topic=data.get("last_clarification_topic", ""),
            # LEGACY
            current_layer=data.get("current_layer", "prebot"),
            conversation_stage=data.get("conversation_stage", "prebot"),
            has_main_pitch=data.get("has_main_pitch", False),
            last_ai_mode=data.get("last_ai_mode", ""),
            preferred_period=data.get("preferred_period", ""),
            followup_count=data.get("followup_count", 0),
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
            user_id=chat_id,
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
        update: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Обработать входящее сообщение.

        FSM-архитектура:
        - PREBOT: скриптовый сбор слотов (без LLM)
        - DIAG_Q: диагностические вопросы (без LLM)
        - NEED_SUMMARY, VALUE_PITCH, COMMIT_TEST: LLM-стадии
        - OBJECTION_HANDLING, CLOSING: сложные LLM-стадии
        - NURTURE, CLOSED: завершающие стадии
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
            context.message_history.append({"role": "user", "content": message_text})

            # Log message
            self.conv_db.save_message(
                conversation_id=context.conversation_id,
                role="user",
                content=message_text,
            )

            # ===== FSM ROUTING =====
            current_stage = context.stage
            logger.info(f"FSM: Processing message, current_stage={current_stage}")

            # PREBOT — скриптовый сбор слотов
            if current_stage == "PREBOT":
                response = await self._process_prebot_fsm(message_text, context)

            # DIAG_Q — диагностические вопросы (без LLM)
            elif current_stage == "DIAG_Q":
                response = await self._process_diag_q(message_text, context)

            # CLOSED — диалог завершён
            elif current_stage == "CLOSED":
                response = await self._process_closed_fsm(message_text, context)

            # LLM-стадии: NEED_SUMMARY, VALUE_PITCH, COMMIT_TEST, PRICE_DISCUSSION,
            # OBJECTION_HANDLING, CLOSING, NURTURE
            else:
                response = await self._process_llm_stage(message_text, context)

            # Сохраняем ответ бота
            if response:
                context.message_history.append(
                    {"role": "assistant", "content": response}
                )

                self.conv_db.save_message(
                    conversation_id=context.conversation_id,
                    role="assistant",
                    content=response,
                )

                # Отправляем в Telegram
                await self._send_telegram_message(chat_id, response, update_id)

            # Сохраняем контекст
            await self.save_context(context)

            return {
                "status": "ok",
                "stage": context.stage,
                "response_sent": True,
                "conversation_id": context.conversation_id,
            }

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

            error_msg = "Извините, произошла ошибка. Попробуйте ещё раз."
            try:
                await self._send_telegram_message(chat_id, error_msg, update_id)
            except Exception:
                pass

            return {"status": "error", "error": str(e), "response_sent": False}

    async def _process_prebot_fsm(self, message: str, context: TwoLayerContext) -> str:
        """
        PREBOT стадия — скриптовый сбор слотов (без LLM).
        После сбора всех слотов переход на DIAG_Q.
        """
        logger.info(f"FSM PREBOT: state={context.prebot_state}")

        # Создаём PreBotContext из TwoLayerContext
        prebot_ctx = PreBotContext(
            state=PreBotState(context.prebot_state),
            slots=context.slots.copy(),
            message_count=context.prebot_message_count,
        )

        # Обрабатываем сообщение
        response, updated_ctx, handoff = self.prebot.process_message(
            message, prebot_ctx
        )

        # Обновляем TwoLayerContext
        context.prebot_state = updated_ctx.state.value
        context.prebot_message_count = updated_ctx.message_count
        context.slots.update(updated_ctx.slots)

        # Проверяем handoff — переход на DIAG_Q
        if handoff:
            logger.info("FSM: PREBOT complete → DIAG_Q")
            context.stage = "DIAG_Q"
            context.diag_question_index = 0

            # Возвращаем первый DIAG_Q вопрос (без LLM!)
            diag_response = get_diag_question(context)
            if diag_response:
                return diag_response
            else:
                # Нет DIAG_Q вопросов — сразу на NEED_SUMMARY
                context.stage = "NEED_SUMMARY"
                return await self._process_llm_stage(message, context)

        return response

    async def _process_diag_q(self, message: str, context: TwoLayerContext) -> str:
        """
        DIAG_Q стадия — диагностические вопросы (БЕЗ LLM!).
        Задаём 2-3 уточняющих вопроса по скрипту.
        """
        logger.info(f"FSM DIAG_Q: question_index={context.diag_question_index}")

        # Определяем следующую стадию через FSM router
        next_stage = decide_next_stage(context, message)
        logger.info(f"FSM DIAG_Q: next_stage={next_stage}")

        # Если FSM решил перейти куда-то (CLOSED, NURTURE, etc.) — переходим
        if next_stage != "DIAG_Q":
            context.stage = next_stage

            # Если переход на LLM-стадию
            if next_stage in ["NEED_SUMMARY", "VALUE_PITCH", "CLOSING", "NURTURE"]:
                return await self._process_llm_stage(message, context)
            elif next_stage == "CLOSED":
                return await self._process_closed_fsm(message, context)

        # Остаёмся в DIAG_Q — выдаём следующий вопрос
        diag_response = get_diag_question(context)
        if diag_response:
            return diag_response
        else:
            # Вопросы закончились — переходим на NEED_SUMMARY
            context.stage = "NEED_SUMMARY"
            context.problem_clarified = True
            return await self._process_llm_stage(message, context)

    async def _process_llm_stage(self, message: str, context: TwoLayerContext) -> str:
        """
        Обработка LLM-стадий: NEED_SUMMARY, VALUE_PITCH, COMMIT_TEST,
        PRICE_DISCUSSION, OBJECTION_HANDLING, CLOSING, NURTURE.

        ВАЖНО: Переход на следующую стадию определяет FSM router (код),
        а НЕ LLM!
        """
        current_stage = context.stage
        logger.info(f"FSM LLM stage: {current_stage}")

        # Сначала определяем следующую стадию через FSM router
        next_stage = decide_next_stage(context, message)
        logger.info(f"FSM: {current_stage} → {next_stage}")

        # Обновляем стадию
        context.stage = next_stage

        # Если перешли в CLOSED — не вызываем LLM
        if next_stage == "CLOSED":
            return await self._process_closed_fsm(message, context)

        # Выбираем модель для стадии
        model = get_stage_model(next_stage)
        logger.info(f"FSM: Using model {model} for stage {next_stage}")

        # Генерируем ответ через LLM
        response = await self._generate_stage_response(
            message, context, next_stage, model
        )

        return response

    async def _process_closed_fsm(self, message: str, context: TwoLayerContext) -> str:
        """CLOSED стадия — диалог завершён."""
        logger.info("FSM CLOSED: contact_captured=" + str(context.contact_captured))

        # Если контакт был захвачен — сохраняем лид
        if context.contact_captured and context.client_contact:
            await self._save_lead(context, source="telegram")

            time_info = ""
            if context.preferred_period:
                time_info = f" ({context.preferred_period})"
            elif context.preferred_time:
                time_info = f" ({context.preferred_time})"

            return f"Принял, спасибо!\n\nМенеджер свяжется{time_info}, чтобы обсудить детали."

        # Если клиент что-то спрашивает после закрытия
        if "?" in message:
            return "Менеджер скоро свяжется и ответит на все вопросы. Если срочное — напишите сюда."

        return "Спасибо! Если появятся вопросы по автоматизации заявок — пишите."

    async def _generate_stage_response(
        self, message: str, context: TwoLayerContext, stage: str, model: str
    ) -> str:
        """
        Генерация ответа LLM для конкретной FSM-стадии.
        Каждая стадия имеет свой промпт.
        """
        slots_summary = self._format_slots_for_prompt(context.slots)

        # Формируем промпт в зависимости от стадии
        stage_prompts = {
            "NEED_SUMMARY": self._get_need_summary_prompt(context, slots_summary),
            "VALUE_PITCH": self._get_value_pitch_prompt(context, slots_summary),
            "COMMIT_TEST": self._get_commit_test_prompt(context, slots_summary),
            "PRICE_DISCUSSION": self._get_price_discussion_prompt(
                context, slots_summary, message
            ),
            "OBJECTION_HANDLING": self._get_objection_prompt(
                context, slots_summary, message
            ),
            "CLOSING": self._get_closing_prompt(context, slots_summary, message),
            "NURTURE": self._get_nurture_prompt(context, slots_summary, message),
        }

        user_prompt = stage_prompts.get(stage, f"[STAGE: {stage}]\n{message}")

        # Вызываем LLM с передачей user_message для anti-interrogation
        return await self._call_llm_for_stage(
            user_prompt, context, stage, model, user_message=message
        )

    def _get_need_summary_prompt(self, context: TwoLayerContext, slots: str) -> str:
        """Промпт для NEED_SUMMARY — резюме потребности."""
        diag_answers = "\n".join(
            [f"- {k}: {v}" for k, v in context.diag_answers.items()]
        )

        return f"""[STAGE: NEED_SUMMARY]

Задача: Сформулируй резюме ситуации клиента в 2-3 предложениях.
Покажи, что ты понял его боль и ситуацию.

Данные клиента:
{slots}

Ответы на диагностику:
{diag_answers if diag_answers else "Нет ответов"}

ФОРМАТ ОТВЕТА:
1. Резюме ситуации (1-2 предложения)
2. Выведенная боль (1 предложение) — НЕ СПРАШИВАЙ, УТВЕРЖДАЙ
3. Мягкий переход: "Верно понимаю ситуацию?"

ЗАПРЕЩЕНО: спрашивать "какие сложности?", называть цены, предлагать созвон."""

    def _get_value_pitch_prompt(self, context: TwoLayerContext, slots: str) -> str:
        """Промпт для VALUE_PITCH — ценностное предложение."""
        leads = context.slots.get("leads_per_month", 100)

        return f"""[STAGE: VALUE_PITCH]

Задача: Покажи ценность ИИ-бота под ситуацию клиента.
НЕ называй цены! Только ценность.

Данные клиента:
{slots}

ФОРМАТ ОТВЕТА (3-4 предложения):
1. Как ИИ-бот решает их конкретную боль
2. Конкретная выгода для их объёма ({leads} заявок)
3. Мягкий вопрос: "Интересно посмотреть, как это работает?"

ЗАПРЕЩЕНО: называть цены, спрашивать про бюджет, давить на созвон."""

    def _get_commit_test_prompt(self, context: TwoLayerContext, slots: str) -> str:
        """Промпт для COMMIT_TEST — проверка интереса."""
        return f"""[STAGE: COMMIT_TEST]

Задача: Проверить готовность клиента к следующему шагу.

Данные клиента:
{slots}

ФОРМАТ ОТВЕТА (2-3 предложения):
"Если тема в целом интересна — можем созвониться на 15 минут, покажу на примере вашей ниши.
Если нет — тоже ок, без навязывания."

ЗАПРЕЩЕНО: давить, называть цены без запроса."""

    def _get_price_discussion_prompt(
        self, context: TwoLayerContext, slots: str, message: str
    ) -> str:
        """Промпт для PRICE_DISCUSSION — обсуждение цены."""
        return f"""[STAGE: PRICE_DISCUSSION]

Клиент спросил про цену: "{message}"

Данные клиента:
{slots}

ТАРИФЫ (называй только по запросу):
• Старт — 25 000 ₽: бот в Telegram, базовый сценарий
• Стандарт — 45-60 000 ₽: + CRM, 30 дней сопровождения
• Про — от 90 000 ₽: несколько сценариев, аналитика

ФОРМАТ ОТВЕТА:
1. Назови тарифы коротко
2. Рекомендуй подходящий под их объём
3. Предложи созвониться: "Хотите обсудить детали?"

Минимум 25 000 ₽. Дешевле не делаем. Скидок нет."""

    def _get_objection_prompt(
        self, context: TwoLayerContext, slots: str, message: str
    ) -> str:
        """Промпт для OBJECTION_HANDLING — обработка возражений."""
        objection_type = context.objection_type
        count = context.price_objection_count

        if objection_type == "price":
            if count <= 1:
                return f"""[STAGE: OBJECTION_HANDLING — цена, первый раз]

Клиент: "{message}"

Данные: {slots}

ФОРМАТ ОТВЕТА:
"Понимаю, сумма ощутимая. При [N] заявках даже 5% потерь — это [X] упущенных клиентов.
Бот обычно окупается за 1-2 месяца. Но если сейчас не в приоритете — ок, можем вернуться позже."

НЕ повторяй презентацию целиком."""
            else:
                return f"""[STAGE: OBJECTION_HANDLING — цена, повторно]

Клиент повторно про цену: "{message}"

ФОРМАТ ОТВЕТА (коротко!):
"Понял. Если ситуация изменится — пишите, буду рад помочь."

НЕ продолжай продавать."""

        elif objection_type == "timing":
            return f"""[STAGE: OBJECTION_HANDLING — время]

Клиент: "{message}"

ФОРМАТ ОТВЕТА:
"Понял, сейчас не самое время. Оставьте контакт — напишу когда будет актуально.
Если нет — тоже ок, без давления."
"""

        elif objection_type == "competitor":
            return f"""[STAGE: OBJECTION_HANDLING — конкурент]

Клиент говорит что есть бот/подрядчик: "{message}"

ФОРМАТ ОТВЕТА:
"Понял, уже работаете с решением. Если захотите усилить или сравнить — пишите.
Удачи с текущим проектом!"

НЕ хейти конкурентов."""

        elif objection_type == "think_about":
            return f"""[STAGE: OBJECTION_HANDLING — подумаю]

Клиент: "{message}"

ФОРМАТ ОТВЕТА:
"Конечно, подумайте. Если будут вопросы — пишите сюда.
Могу скинуть краткое описание, чтобы было что показать коллегам?"
"""

        return f"""[STAGE: OBJECTION_HANDLING]

Клиент: "{message}"

Отработай возражение мягко, без давления. 2-3 предложения."""

    def _get_closing_prompt(
        self, context: TwoLayerContext, slots: str, message: str
    ) -> str:
        """Промпт для CLOSING — закрытие на контакт."""
        attempts = context.closing_attempts

        if attempts == 0:
            return """[STAGE: CLOSING — первый вопрос]

Клиент готов к следующему шагу.

ФОРМАТ ОТВЕТА (одно предложение):
"Когда удобнее созвониться — в будни днём или вечером?"

ТОЛЬКО этот вопрос. Ничего больше."""

        elif attempts == 1:
            return f"""[STAGE: CLOSING — контакт]

Клиент ответил про время: "{message}"

ФОРМАТ ОТВЕТА:
"Отлично. Оставьте контакт (Telegram или телефон), передам менеджеру."
"""

        else:
            return f"""[STAGE: CLOSING — финал]

Время: {context.preferred_time or context.preferred_period or message}

ФОРМАТ ОТВЕТА:
"Принял, спасибо! Менеджер свяжется, чтобы согласовать детали."
"""

    def _get_nurture_prompt(
        self, context: TwoLayerContext, slots: str, message: str
    ) -> str:
        """Промпт для NURTURE — мягкое сопровождение."""
        offers = context.nurture_offers_made

        if offers == 0:
            return """[STAGE: NURTURE — первое предложение]

Клиент пока не готов.

ФОРМАТ ОТВЕТА:
"Понял. Могу прислать чек-лист по автоматизации заявок — пригодится когда тема станет актуальной.
Куда удобнее: сюда или на почту?"
"""
        else:
            return """[STAGE: NURTURE — завершение]

ФОРМАТ ОТВЕТА:
"Хорошо, если появятся вопросы — пишите сюда. Удачи!"
"""

    async def _call_llm_for_stage(
        self,
        user_prompt: str,
        context: TwoLayerContext,
        stage: str,
        model: str,
        user_message: str = "",
    ) -> str:
        """
        Вызов LLM для конкретной стадии с ЗАЩИТОЙ ОТ ДОПРОСА.

        Логика:
        1. Добавляем в промпт информацию о clarification_attempts
        2. После получения ответа проверяем — это уточняющий вопрос?
        3. Если да и attempts >= 2 — перегенерируем с force_move_prompt
        """
        try:
            system_prompt = self.get_ai_seller_prompt()

            # Формируем историю сообщений (последние 10)
            messages = []
            for msg in context.message_history[-10:]:
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )

            # === ANTI-INTERROGATION: Добавляем инструкцию в промпт ===
            clarification_instruction = get_simplified_prompt_instruction(
                context.clarification_attempts
            )
            if clarification_instruction:
                user_prompt = f"{clarification_instruction}\n\n{user_prompt}"

            # Добавляем информацию о счётчике в промпт
            user_prompt += (
                f"\n\n[CLARIFICATION_ATTEMPTS: {context.clarification_attempts}]"
            )

            messages.append({"role": "user", "content": user_prompt})

            # Контекст для оркестратора
            llm_context = {
                "chat_id": context.chat_id,
                "stage": stage,
                "leads_per_month": context.slots.get("leads_per_month"),
                "niche": context.slots.get("niche"),
                "clarification_attempts": context.clarification_attempts,
            }

            # Вызываем LLM с выбранной моделью
            response = await llm_generate_response(
                mode=stage.lower(),
                context=llm_context,
                messages=messages,
                system_prompt=system_prompt,
                model_override=model,
            )

            # === POST-PROCESSING: Проверяем ответ на уточняющие вопросы ===
            response = await self._post_process_clarification(
                response, context, stage, model, user_message
            )

            return response

        except Exception as e:
            logger.error(f"LLM error in stage {stage}: {e}", exc_info=True)
            return "Извините, возникла техническая ошибка. Можете повторить?"

    async def _post_process_clarification(
        self,
        response: str,
        context: TwoLayerContext,
        stage: str,
        model: str,
        user_message: str,
    ) -> str:
        """
        Пост-обработка LLM ответа для защиты от допроса.

        Правила:
        - Если ответ НЕ уточняющий вопрос → сбрасываем счётчик, возвращаем как есть
        - Если уточняющий и attempts == 0 → разрешаем, attempts = 1
        - Если уточняющий и attempts == 1 → разрешаем, attempts = 2
        - Если уточняющий и attempts >= 2 → ЗАПРЕЩАЕМ, перегенерируем
        """
        is_clarification = is_clarification_question(response)

        logger.info(
            f"Anti-interrogation check: is_clarification={is_clarification}, "
            f"attempts={context.clarification_attempts}, stage={stage}"
        )

        # Если это НЕ уточняющий вопрос — сбрасываем счётчик
        if not is_clarification:
            if context.clarification_attempts > 0:
                logger.info(
                    f"Clarification counter reset (was {context.clarification_attempts})"
                )
            context.clarification_attempts = 0
            context.last_clarification_topic = ""
            return response

        # Это УТОЧНЯЮЩИЙ вопрос
        current_attempts = context.clarification_attempts

        if current_attempts == 0:
            # Первый уточняющий вопрос — разрешаем
            context.clarification_attempts = 1
            context.last_clarification_topic = (
                user_message[:50] if user_message else "generic"
            )
            logger.info(
                f"First clarification question allowed, topic={context.last_clarification_topic}"
            )
            return response

        elif current_attempts == 1:
            # Второй уточняющий вопрос — разрешаем, но фиксируем
            context.clarification_attempts = 2
            logger.info("Second clarification question allowed (simplified expected)")
            return response

        else:
            # ТРЕТИЙ+ уточняющий вопрос — ЗАПРЕЩЕНО!
            logger.warning(
                f"BLOCKING third clarification question! Forcing move forward. "
                f"Blocked response: {response[:100]}..."
            )

            # Перегенерируем с force_move_prompt
            force_prompt = get_force_move_prompt(context, user_message)
            new_response = await self._regenerate_without_clarification(
                force_prompt, context, stage, model
            )

            # Сбрасываем счётчик после принудительного перехода
            context.clarification_attempts = 0
            context.last_clarification_topic = ""

            return new_response

    async def _regenerate_without_clarification(
        self, force_prompt: str, context: TwoLayerContext, stage: str, model: str
    ) -> str:
        """
        Перегенерировать ответ с явным запретом уточняющих вопросов.
        """
        try:
            system_prompt = self.get_ai_seller_prompt()

            messages = []
            for msg in context.message_history[-10:]:
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )

            messages.append({"role": "user", "content": force_prompt})

            llm_context = {
                "chat_id": context.chat_id,
                "stage": stage,
                "force_no_clarification": True,
            }

            response = await llm_generate_response(
                mode=stage.lower(),
                context=llm_context,
                messages=messages,
                system_prompt=system_prompt,
                model_override=model,
            )

            logger.info(
                f"Regenerated response without clarification: {response[:100]}..."
            )

            # Проверяем что новый ответ не содержит уточнений
            if is_clarification_question(response):
                # Если всё ещё уточняющий — даём fallback
                logger.warning(
                    "Regenerated response still contains clarification! Using fallback."
                )
                return (
                    "Понял вас. Давайте я расскажу, как наш бот может помочь в вашей ситуации. "
                    "Он автоматически обрабатывает заявки и собирает нужную информацию. "
                    "Интересно посмотреть, как это работает на практике?"
                )

            return response

        except Exception as e:
            logger.error(f"Error regenerating response: {e}", exc_info=True)
            return (
                "Понял. Давайте я покажу, как бот решает подобные задачи — "
                "он собирает информацию от клиентов автоматически. "
                "Хотите посмотреть пример?"
            )

    # ===== LEGACY METHODS (для обратной совместимости) =====

    async def _process_prebot(self, message: str, context: TwoLayerContext) -> str:
        """Legacy: Обработка скриптовым pre-bot"""
        return await self._process_prebot_fsm(message, context)

    async def _process_ai_seller(self, message: str, context: TwoLayerContext) -> str:
        """Обработка AI-продавцом (LLM) с режимами"""
        logger.info(f"AI Seller layer, stage={context.conversation_stage}")

        # Извлекаем бюджет если есть
        self._extract_budget(message, context)

        # --- Проверяем, спрашивает ли клиент о цене ---
        if is_price_question(message):
            context.user_asked_price_recently = True
            logger.info("Price question detected — user_asked_price_recently = True")

        # --- Проверяем, ответил ли клиент на вопрос о проблеме ---
        if not context.problem_clarified and len(context.message_history) >= 2:
            # Берём предпоследнее сообщение (последний вопрос бота)
            last_bot_msg = None
            for msg in reversed(context.message_history[:-1]):
                if msg.get("role") == "assistant":
                    last_bot_msg = msg.get("content", "").lower()
                    break

            if last_bot_msg:
                # Проверяем признаки вопроса о сложностях
                problem_keywords = [
                    "сложност",
                    "проблем",
                    "болит",
                    "труднее всего",
                    "больше всего мешает",
                    "главная боль",
                ]
                asked_about_problem = any(kw in last_bot_msg for kw in problem_keywords)

                # Проверяем что клиент дал содержательный ответ
                msg_lower = message.lower()
                is_meaningful = len(message.strip()) > 10 and not any(
                    skip in msg_lower
                    for skip in ["не знаю", "незнаю", "хз", "не могу сказать"]
                )

                if asked_about_problem and is_meaningful:
                    context.problem_clarified = True
                    logger.info(
                        "Problem clarified - client answered about main difficulty"
                    )

        # --- ВАЖНО: Проверяем контактные данные в любой момент ---
        # Если клиент присылает контакт (@username, телефон) — закрываем диалог
        contact = extract_contact(message)
        if contact or is_contact_info(message):
            context.client_contact = contact or message.strip()
            context.contact_captured = True
            context.conversation_stage = "closed"
            logger.info(
                f"Contact detected in ai_seller: {context.client_contact}, stage -> closed"
            )

            # Сохраняем заявку
            await self._save_lead(context, source="telegram")

            # Формируем финальный ответ
            time_info = ""
            if context.preferred_period:
                time_info = f" ({context.preferred_period})"
            elif context.preferred_time:
                time_info = f" ({context.preferred_time})"

            return f"Принял контакт, спасибо!\n\nПередам менеджеру, он свяжется{time_info}, чтобы обсудить детали."

        # --- Проверяем soft opt-out: "не рассматриваю запуск", "не актуально" ---
        if is_not_now(message) and not context.soft_opt_out:
            context.soft_opt_out = True
            logger.info("Soft opt-out detected")

            # Если контакт уже есть — просто мягко завершаем
            if context.contact_captured:
                context.conversation_stage = "closed"
                return "Понял, без проблем. Контакт у меня есть — если тема станет актуальной, можете просто написать сюда, буду на связи."

            # Контакта нет — предлагаем оставить
            return "Ок, сейчас не самое время. Чтобы не потеряться, давайте оставим контакт — если тема станет актуальной, я смогу написать. Куда удобнее: Telegram или WhatsApp?"

        # --- Проверяем шумовой ответ после soft_opt_out ---
        if context.soft_opt_out and looks_like_noise(message):
            # Короткий ответ типа "ок", "угу" после soft opt-out
            if context.contact_captured:
                context.conversation_stage = "closed"
                return "Принял, спасибо! Контакт есть, если захотите вернуться к теме автоматизации — просто напишите сюда."
            else:
                # Ещё раз мягко попросим контакт
                return "Если оставите контакт (Telegram или телефон), смогу написать когда будет актуально."

        # Определяем режим
        mode = self._detect_ai_mode(message, context)
        logger.info(
            f"AI Seller mode detected: {mode}, price_objection_count={context.price_objection_count}"
        )

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

        # После ответа на возражение по цене — ставим флаг
        if mode == "price_objection":
            context.roi_explained = True
            logger.info("ROI explained — roi_explained = True")

        # Инкрементируем счётчик followup (для предотвращения допроса)
        if mode == "followup":
            context.followup_count += 1
            logger.info(f"Followup count incremented to {context.followup_count}")

        # Режим closing — сразу переходим на стадию ожидания времени
        # (не зависим от парсинга текста ответа)
        if mode == "closing":
            context.conversation_stage = "closing_wait_time"
            logger.info("Stage -> closing_wait_time (mode=closing)")

        context.last_ai_mode = mode

        return response

    async def _process_closing_stage(
        self, message: str, context: TwoLayerContext
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
        logger.info(
            f"Closing stage: {stage}, closing_attempts={context.closing_attempts}"
        )

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
        self, message: str, context: TwoLayerContext
    ) -> str:
        """Обработка ожидания выбора времени."""

        # 1. Проверяем конкретный день/время
        if has_specific_day_or_time(message):
            context.preferred_time = message.strip()
            context.conversation_stage = "closing_wait_contact"
            context.closing_attempts = 0  # сбрасываем
            logger.info(
                f"Specific time: {context.preferred_time}, stage -> closing_wait_contact"
            )

            return f"Отлично, записал: {context.preferred_time}.\n\nОставьте, пожалуйста, контакт (Telegram/WhatsApp или телефон), куда удобнее написать — передам менеджеру."

        # 2. Проверяем расплывчатый период ("на следующей неделе")
        vague_period = extract_vague_period(message)
        if vague_period or is_vague_time_period(message):
            context.preferred_period = vague_period or message.strip()
            context.closing_attempts += 1
            logger.info(
                f"Vague period: {context.preferred_period}, attempts={context.closing_attempts}"
            )

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
            logger.info(
                f"Time preference: {context.preferred_time}, stage -> closing_wait_contact"
            )

            period_info = (
                f" {context.preferred_period}" if context.preferred_period else ""
            )
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
        self, message: str, context: TwoLayerContext
    ) -> str:
        """Обработка ожидания контакта."""
        contact = extract_contact(message)

        if contact or is_contact_info(message):
            context.client_contact = contact or message.strip()
            context.contact_captured = True
            context.conversation_stage = "closed"
            logger.info(f"Contact received: {context.client_contact}, stage -> closed")

            # Сохраняем заявку
            await self._save_lead(context, source="telegram")

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
        self, message: str, context: TwoLayerContext
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
               - явный сигнал (давайте созвонимся)
               - положительное подтверждение после main_pitch (да, бывает, важно)
               - достигнут лимит followup (2 вопроса = хватит допроса)
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
            logger.debug("Mode: closing (explicit closing signal)")
            return "closing"

        # 2b. Положительное подтверждение после main_pitch = переходим к closing
        # "да", "бывает", "важно", "иногда" — клиент подтвердил интерес
        if is_positive_confirmation(message):
            logger.info(
                f"Mode: closing (positive confirmation after {context.followup_count} followups)"
            )
            return "closing"

        # 2c. Лимит followup достигнут — хватит допроса, переходим к closing
        # Максимум 2 followup-сообщения, потом закрываем
        if context.followup_count >= 2:
            logger.info(
                f"Mode: closing (followup limit reached: {context.followup_count})"
            )
            return "closing"

        # 3. Возражения по приоритету
        if is_price_objection(message):
            logger.debug(
                f"Mode: price_objection (count={context.price_objection_count})"
            )
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
        logger.debug(f"Mode: followup (count={context.followup_count})")
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
        self, message: str, context: TwoLayerContext, mode: str
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
            mode_instruction = """[MODE: main_pitch]
ЗАДАЧА: Резюме ситуации → Выведенная боль → Решение → Мягкий вопрос.
ЗАПРЕЩЕНО: спрашивать "какие сложности?", называть цены, предлагать созвон.
Следуй скрипту из системного промпта. 4-5 предложений максимум.
"""
        else:
            mode_instruction = """[MODE: followup]
Отреагируй на ответ клиента. НЕ задавай новый вопрос — сначала дай ценность.
Если клиент подтвердил ("да", "бывает") — предложи посмотреть, как работает.
Если нейтрально — кратко покажи пользу и дай выбор.
Коротко, 2-3 предложения.
"""

        # Проверяем флаг problem_clarified
        problem_clarified_warning = ""
        if context.problem_clarified:
            problem_clarified_warning = "ВАЖНО: Клиент УЖЕ назвал главную проблему. НЕ переспрашивай про сложности.\n"

        # Формируем блок флагов для LLM
        flags_block = f"""[ФЛАГИ]
USER_ASKED_PRICE_RECENTLY: {context.user_asked_price_recently}
ROI_EXPLAINED: {context.roi_explained}
"""

        user_prompt = f"""{mode_instruction}
{problem_clarified_warning}
{flags_block}
Данные о клиенте:
{slots_summary}

{budget_warning}

Сообщение клиента: "{message}"

Ответь как AI-продавец SalesWhisper. Один вопрос за раз. Не повторяй вопросы."""

        model = select_model(mode, context.slots)
        logger.info(f"AI Seller response: mode={mode}, model={model}")

        return await self._call_llm_with_mode(user_prompt, context, mode)

    async def _call_llm_with_mode(
        self, user_prompt: str, context: TwoLayerContext, mode: str
    ) -> str:
        """Вызвать LLM через новый оркестратор с режимом"""
        try:
            system_prompt = self.get_ai_seller_prompt()

            # Формируем сообщения для оркестратора
            messages = []
            for msg in context.message_history[-10:]:
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )

            # Добавляем текущий промпт
            messages.append({"role": "user", "content": user_prompt})

            # Формируем контекст для выбора модели
            llm_context = {
                "chat_id": context.chat_id,
                "leads_per_month": context.slots.get("leads_per_month"),
                "decision_maker": context.slots.get("decision_maker"),
                "niche": context.slots.get("niche"),
                "has_main_pitch": context.has_main_pitch,
            }

            # Вызываем новый оркестратор
            response = await llm_generate_response(
                mode=mode,
                context=llm_context,
                messages=messages,
                system_prompt=system_prompt,
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
            "budget_user": "Бюджет клиента",
        }

        lines = []
        for key, label in slot_names.items():
            if key in slots and slots[key]:
                value = slots[key]
                if key == "decision_maker":
                    value = {
                        "self": "сам клиент",
                        "partner": "партнёр",
                        "owner": "руководитель",
                    }.get(value, value)
                lines.append(f"• {label}: {value}")

        # Добавляем средний чек (из данных клиента или оценка по нише)
        avg_check = parse_avg_check(slots)
        if avg_check:
            lines.append(
                f"• Средний чек (от клиента): {avg_check:,} ₽".replace(",", " ")
            )
        else:
            estimated_check = estimate_avg_check_from_niche(slots)
            if estimated_check:
                lines.append(
                    f"• Средний чек (оценка по нише): ~{estimated_check:,} ₽".replace(
                        ",", " "
                    )
                )
            else:
                lines.append("• Средний чек: неизвестен (НЕЛЬЗЯ придумывать число!)")

        return "\n".join(lines) if lines else "Нет данных"

    async def _send_telegram_message(
        self, chat_id: int, text: str, update_id: Optional[int] = None
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

    async def _save_lead(self, context: TwoLayerContext, source: str = "telegram"):
        """Сохранить заявку в единое хранилище"""
        try:
            from services.leads_storage import (
                get_leads_storage,
                Lead,
                detect_contact_type,
            )

            storage = await get_leads_storage()

            # Определяем тип контакта
            contact_type = detect_contact_type(context.client_contact)

            # Собираем данные из слотов
            name = context.slots.get("name", "Неизвестно")
            niche = context.slots.get("niche", "")
            leads_per_month = context.slots.get("leads_per_month")
            pain = context.slots.get("pain", "")

            # Формируем сообщение с дополнительной информацией
            message_parts = []
            if niche:
                message_parts.append(f"Ниша: {niche}")
            if leads_per_month:
                message_parts.append(f"Заявок в месяц: {leads_per_month}")
            if pain:
                message_parts.append(f"Боль: {pain}")
            if context.preferred_period:
                message_parts.append(f"Когда связаться: {context.preferred_period}")

            lead = Lead(
                id=0,
                source=source,
                name=name,
                contact=context.client_contact,
                contact_type=contact_type,
                niche=niche,
                leads_per_month=leads_per_month,
                pain=pain,
                message="\n".join(message_parts) if message_parts else None,
                conversation_id=context.conversation_id,
            )

            result = await storage.save_lead(lead)
            logger.info(f"Lead saved from {source}: {result}")

        except Exception as e:
            logger.error(f"Error saving lead: {e}", exc_info=True)

    async def process_web_message(
        self, session_id: str, message_text: str
    ) -> Dict[str, Any]:
        """
        Обработать сообщение из веб-чата (FSM-архитектура).

        Args:
            session_id: Уникальный ID сессии веб-чата
            message_text: Текст сообщения от пользователя

        Returns:
            Dict с response (текст ответа бота) и metadata
        """
        # Генерируем fake chat_id из session_id (используем hash)
        import hashlib

        chat_id = int(hashlib.sha256(session_id.encode()).hexdigest()[:12], 16)

        try:
            # Загружаем контекст (тот же Redis key, что и для Telegram)
            context = await self.get_context(chat_id)
            context.user_id = chat_id
            context.total_message_count += 1
            context.last_message_at = time.time()

            # Сохраняем сообщение пользователя
            context.message_history.append({"role": "user", "content": message_text})

            # Log message
            self.conv_db.save_message(
                conversation_id=context.conversation_id,
                role="user",
                content=message_text,
            )

            # ===== FSM ROUTING (идентично Telegram) =====
            current_stage = context.stage
            logger.info(f"FSM Web: Processing message, current_stage={current_stage}")

            # PREBOT — скриптовый сбор слотов
            if current_stage == "PREBOT":
                response = await self._process_prebot_fsm(message_text, context)

            # DIAG_Q — диагностические вопросы (без LLM)
            elif current_stage == "DIAG_Q":
                response = await self._process_diag_q(message_text, context)

            # CLOSED — диалог завершён
            elif current_stage == "CLOSED":
                response = await self._process_closed_fsm(message_text, context)

            # LLM-стадии
            else:
                response = await self._process_llm_stage(message_text, context)

            # Сохраняем ответ бота
            if response:
                context.message_history.append(
                    {"role": "assistant", "content": response}
                )

                self.conv_db.save_message(
                    conversation_id=context.conversation_id,
                    role="assistant",
                    content=response,
                )

            # Сохраняем контекст
            await self.save_context(context)

            return {
                "status": "ok",
                "response": response or "",
                "stage": context.stage,
                "conversation_id": context.conversation_id,
            }

        except Exception as e:
            logger.error(f"Error processing web message: {e}", exc_info=True)
            return {
                "status": "error",
                "response": "Извините, произошла ошибка. Попробуйте ещё раз.",
                "error": str(e),
            }


# Глобальный инстанс
_two_layer_manager: Optional[TwoLayerFlowManager] = None


def get_two_layer_flow_manager() -> TwoLayerFlowManager:
    """Получить TwoLayerFlowManager"""
    global _two_layer_manager
    if _two_layer_manager is None:
        _two_layer_manager = TwoLayerFlowManager()
    return _two_layer_manager
