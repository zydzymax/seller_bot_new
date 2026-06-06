"""
webhook.py — Telegram webhook с middleware для SoVAni AI-продавца.

- FastAPI webhook обработчик для Telegram
- Rate limiting и идемпотентность по update_id
- Input санитизация и security middleware 
- Интеграция с FSM и flow manager
- Prometheus метрики и structured logging

© SoVAni 2025
"""

import os
import json
import time
import asyncio
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import aiohttp
from datetime import datetime
from zoneinfo import ZoneInfo

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка uvloop для повышения производительности
try:
    import uvloop

    uvloop.install()
    print("uvloop installed for enhanced performance")
except ImportError:
    print("uvloop not available, using default asyncio loop")

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import hmac
import uvicorn
from pydantic import BaseModel, Field

from utils.logging import get_logger, set_context
from utils.rate_limit import get_rate_limiter
from utils.idempotency import get_idempotency_manager
from utils.input_sanitizer import get_sanitizer
from utils.metrics import get_metrics_collector, get_health_checker, track_duration
from utils.env_resolver import get_redis_url
from llm.orchestrator import get_orchestrator
from dialog.flow_router import (
    get_flow_manager,
)  # Router to select flow manager by domain
from adapters.crm_adapter import get_crm_adapter

# AmoCRM/Kommo OAuth and Widget API
try:
    from services.amocrm_oauth import router as amocrm_oauth_router
    from services.widget_api_v2 import router as widget_api_router

    AMOCRM_ROUTERS_AVAILABLE = True
except ImportError:
    AMOCRM_ROUTERS_AVAILABLE = False
    amocrm_oauth_router = None
    widget_api_router = None

# Licensing and Payment routers
try:
    from services.licensing import router as licensing_router
    from services.payment import router as payment_router

    PAYMENT_ROUTERS_AVAILABLE = True
except ImportError:
    PAYMENT_ROUTERS_AVAILABLE = False
    licensing_router = None
    payment_router = None

logger = get_logger(__name__)

# Безопасный импорт middleware с fallback
setup_middlewares = None
try:
    from .middleware.ratelimit import setup_middlewares

    logger.info("ratelimit_middleware_imported_successfully")
except Exception as e:
    logger.warning("ratelimit_middleware_unavailable", error=str(e))


class TelegramUpdate(BaseModel):
    """Модель Telegram Update"""

    update_id: int
    message: Optional[Dict[str, Any]] = None
    edited_message: Optional[Dict[str, Any]] = None
    channel_post: Optional[Dict[str, Any]] = None
    edited_channel_post: Optional[Dict[str, Any]] = None
    inline_query: Optional[Dict[str, Any]] = None
    chosen_inline_result: Optional[Dict[str, Any]] = None
    callback_query: Optional[Dict[str, Any]] = None


class WebhookResponse(BaseModel):
    """Стандартный ответ webhook"""

    status: str = "ok"
    message: Optional[str] = None
    update_id: Optional[int] = None
    processed_at: float = Field(default_factory=time.time)


class TelegramWebhookProcessor:
    """Обработчик Telegram webhook с полным pipeline"""

    def __init__(self):
        self.rate_limiter = None
        self.idempotency_manager = None
        self.sanitizer = None
        self.llm_orchestrator = None
        self.flow_manager = None
        self.crm_adapter = None
        self.metrics_collector = None
        self.stt_service = None
        self.stt_unavailable = False

    async def initialize(self):
        """Инициализация всех компонентов"""
        self.rate_limiter = await get_rate_limiter()
        self.idempotency_manager = await get_idempotency_manager()
        self.sanitizer = get_sanitizer()
        self.llm_orchestrator = await get_orchestrator()
        self.flow_manager = await get_flow_manager()
        self.crm_adapter = await get_crm_adapter()
        self.metrics_collector = await get_metrics_collector()

        logger.info("Telegram webhook processor initialized")

    def extract_message_data(self, update: TelegramUpdate) -> Optional[Dict[str, Any]]:
        """Извлечение данных сообщения из update"""
        message_data = None

        if update.message:
            message_data = update.message
        elif update.edited_message:
            message_data = update.edited_message
        elif update.callback_query and update.callback_query.get("message"):
            message_data = update.callback_query["message"]
            message_data["callback_data"] = update.callback_query.get("data")

        return message_data

    def extract_user_info(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Извлечение информации о пользователе"""
        from_user = message_data.get("from", {})
        chat = message_data.get("chat", {})

        return {
            "user_id": from_user.get("id"),
            "username": from_user.get("username"),
            "first_name": from_user.get("first_name"),
            "last_name": from_user.get("last_name"),
            "language_code": from_user.get("language_code", "ru"),
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
            "is_bot": from_user.get("is_bot", False),
        }

    def extract_text_content(
        self, update: TelegramUpdate, message_data: Dict[str, Any]
    ) -> str:
        """Извлечение текста из update (text/callback/caption)"""
        if update.callback_query:
            return message_data.get("callback_data", "") or ""

        text = message_data.get("text")
        if text:
            return text

        caption = message_data.get("caption")
        if caption:
            return caption

        return ""

    def detect_message_kind(self, message_data: Dict[str, Any]) -> str:
        """Определение типа входящего сообщения"""
        if message_data.get("text"):
            return "text"
        if message_data.get("photo"):
            return "photo"
        if message_data.get("video"):
            return "video"
        if message_data.get("document"):
            return "document"
        if message_data.get("voice"):
            return "voice"
        if message_data.get("audio"):
            return "audio"
        if message_data.get("sticker"):
            return "sticker"
        return "unknown"

    def _extract_file_meta(
        self, message_data: Dict[str, Any], message_kind: str
    ) -> tuple[Optional[str], int]:
        """Извлечь file_id и duration для voice/audio/video."""
        media_payload = message_data.get(message_kind)
        if not isinstance(media_payload, dict):
            return None, 0

        file_id = media_payload.get("file_id")
        duration = int(media_payload.get("duration") or 0)
        return file_id, duration

    async def _get_stt_service(self):
        """Ленивая инициализация STT сервиса."""
        if self.stt_service is not None:
            return self.stt_service
        if self.stt_unavailable:
            return None

        try:
            from audio.speech_to_text import create_stt_service

            provider_name = os.getenv("STT_PROVIDER", "openai")
            self.stt_service = create_stt_service(provider_name)
            return self.stt_service
        except Exception as e:
            self.stt_unavailable = True
            logger.warning("stt_service_unavailable", error=str(e))
            return None

    async def transcribe_media_message(
        self, message_data: Dict[str, Any], message_kind: str
    ) -> str:
        """
        Транскрибировать media сообщение (voice/audio/video).

        Возвращает пустую строку, если транскрипция недоступна или неуспешна.
        """
        if message_kind not in {"voice", "audio", "video"}:
            return ""

        stt_service = await self._get_stt_service()
        if not stt_service:
            return ""

        file_id, duration = self._extract_file_meta(message_data, message_kind)
        if not file_id:
            return ""

        try:
            if message_kind == "voice":
                transcript = await stt_service.transcribe_telegram_voice(file_id, duration)
            elif message_kind == "audio":
                transcript = await stt_service.transcribe_telegram_audio(file_id, duration)
            else:
                max_duration = int(os.getenv("VIDEO_STT_MAX_DURATION_SEC", "300"))
                if duration > max_duration:
                    logger.info(
                        "video_transcription_skipped_duration",
                        duration=duration,
                        max_duration=max_duration,
                    )
                    return ""
                media_bytes = await stt_service.download_telegram_voice(file_id)
                transcript = await stt_service.provider.transcribe(media_bytes, "mp4")

            if not transcript:
                return ""

            cleaned = transcript.strip()
            if not cleaned or cleaned.startswith("⚠️"):
                return ""

            logger.info(
                "media_transcription_success",
                message_kind=message_kind,
                duration=duration,
                text_len=len(cleaned),
            )
            return cleaned

        except Exception as e:
            logger.warning(
                "media_transcription_failed",
                message_kind=message_kind,
                error=str(e),
            )
            return ""

    async def describe_photo_content(self, message_data: Dict[str, Any]) -> str:
        """Опционально получить краткое описание фото через OpenAI vision."""
        vision_enabled = os.getenv("PHOTO_VISION_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not vision_enabled:
            return ""

        photos = message_data.get("photo") or []
        if not photos:
            return ""

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        openai_key = os.getenv("OPENAI_API_KEY")
        if not telegram_token or not openai_key:
            return ""

        try:
            largest = photos[-1]
            file_id = largest.get("file_id")
            return await self._describe_image_by_file_id(
                file_id=file_id,
                telegram_token=telegram_token,
                openai_key=openai_key,
            )

        except Exception as e:
            logger.warning("photo_vision_analysis_failed", error=str(e))
            return ""

    async def describe_document_image_content(self, message_data: Dict[str, Any]) -> str:
        """Описание изображения, присланного как document."""
        vision_enabled = os.getenv("PHOTO_VISION_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not vision_enabled:
            return ""

        document = message_data.get("document") or {}
        if not isinstance(document, dict):
            return ""

        mime_type = (document.get("mime_type") or "").lower()
        if mime_type and not mime_type.startswith("image/"):
            return ""

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        openai_key = os.getenv("OPENAI_API_KEY")
        if not telegram_token or not openai_key:
            return ""

        file_id = document.get("file_id")
        try:
            return await self._describe_image_by_file_id(
                file_id=file_id,
                telegram_token=telegram_token,
                openai_key=openai_key,
            )
        except Exception as e:
            logger.warning("document_vision_analysis_failed", error=str(e))
            return ""

    async def _describe_image_by_file_id(
        self, file_id: Optional[str], telegram_token: str, openai_key: str
    ) -> str:
        """Общее описание изображения по Telegram file_id."""
        if not file_id:
            return ""

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"https://api.telegram.org/bot{telegram_token}/getFile",
                params={"file_id": file_id},
            ) as file_resp:
                if file_resp.status != 200:
                    return ""
                file_json = await file_resp.json()
                file_path = (
                    file_json.get("result", {}) if isinstance(file_json, dict) else {}
                ).get("file_path")
                if not file_path:
                    return ""

            image_url = f"https://api.telegram.org/file/bot{telegram_token}/{file_path}"
            vision_model = os.getenv("PHOTO_VISION_MODEL", "gpt-4o-mini")
            payload = {
                "model": vision_model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Кратко опиши изображение на русском (1-2 предложения), "
                                    "чтобы AI-продавец мог продолжить диалог по делу."
                                ),
                            },
                            {"type": "input_image", "image_url": image_url},
                        ],
                    }
                ],
                "max_output_tokens": 120,
            }
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            }
            async with session.post(
                "https://api.openai.com/v1/responses",
                json=payload,
                headers=headers,
            ) as vision_resp:
                if vision_resp.status != 200:
                    return ""
                vision_json = await vision_resp.json()
                summary = (vision_json or {}).get("output_text", "")
                return (summary or "").strip()

    @track_duration("telegram_updates_duration_seconds")
    async def process_update(self, update: TelegramUpdate) -> WebhookResponse:
        """
        Основная обработка Telegram update

        Args:
            update: Telegram update объект

        Returns:
            WebhookResponse с результатом обработки
        """
        start_time = time.time()

        # Увеличиваем счетчик обработанных updates
        self.metrics_collector.increment_counter("telegram_updates_total")

        try:
            # Установка контекста для логирования
            set_context(update_id=update.update_id, component="telegram_webhook")

            logger.info("Processing Telegram update", update_id=update.update_id)

            # Проверка идемпотентности
            idem_result = await self.idempotency_manager.check_duplicate(
                "telegram_update", str(update.update_id)
            )

            if idem_result.is_duplicate:
                logger.info("Duplicate update ignored", update_id=update.update_id)
                self.metrics_collector.increment_counter("idempotency_duplicates_total")
                return WebhookResponse(
                    status="duplicate",
                    message="Update already processed",
                    update_id=update.update_id,
                )

            # Отметка начала обработки
            if not await self.idempotency_manager.mark_processing(
                "telegram_update",
                str(update.update_id),
                ttl=300,  # 5 минут TTL для обработки
            ):
                logger.warning(
                    "Update processing race condition", update_id=update.update_id
                )
                return WebhookResponse(
                    status="processing",
                    message="Update is being processed",
                    update_id=update.update_id,
                )

            # Извлечение данных сообщения
            message_data = self.extract_message_data(update)

            if not message_data:
                logger.info(
                    "No processable message in update", update_id=update.update_id
                )
                await self.idempotency_manager.mark_completed(
                    "telegram_update", str(update.update_id), "no_message"
                )
                return WebhookResponse(
                    status="ignored",
                    message="No processable message",
                    update_id=update.update_id,
                )

            # Извлечение информации о пользователе
            user_info = self.extract_user_info(message_data)
            chat_id = user_info["chat_id"]

            # Обновление контекста логирования
            set_context(
                update_id=update.update_id,
                chat_id=chat_id,
                user_id=user_info["user_id"],
                component="telegram_webhook",
            )

            # Проверка rate limiting по chat_id
            allowed, rate_metadata = await self.rate_limiter.is_allowed(
                str(chat_id), "chat"
            )

            if not allowed:
                logger.warning(
                    "Rate limit exceeded for chat",
                    chat_id=chat_id,
                    retry_after=rate_metadata.get("retry_after"),
                )

                self.metrics_collector.increment_counter("rate_limit_blocked_total")

                # Все равно помечаем как обработанный чтобы избежать повторов
                await self.idempotency_manager.mark_completed(
                    "telegram_update", str(update.update_id), "rate_limited"
                )

                return WebhookResponse(
                    status="rate_limited",
                    message=f"Too many requests. Try again in {rate_metadata.get('retry_after', 60)} seconds.",
                    update_id=update.update_id,
                )

            # Извлечение и санитизация текста сообщения
            message_kind = self.detect_message_kind(message_data)
            text_content = self.extract_text_content(update, message_data)

            # Если медиа пришло без подписи — не теряем апдейт, передаем в flow
            if not text_content and message_kind in {
                "photo",
                "video",
                "document",
                "voice",
                "audio",
            }:
                if message_kind == "photo":
                    photo_summary = await self.describe_photo_content(message_data)
                    if photo_summary:
                        text_content = (
                            "Пользователь отправил фото без текста. "
                            f"Описание изображения: {photo_summary}. "
                            "Ответь по контексту и при необходимости задай один уточняющий вопрос."
                        )
                    else:
                        text_content = (
                            "Пользователь отправил фото без текста. "
                            "Попроси кратко уточнить запрос."
                        )
                elif message_kind == "document":
                    document_summary = await self.describe_document_image_content(
                        message_data
                    )
                    if document_summary:
                        text_content = (
                            "Пользователь отправил изображение файлом без текста. "
                            f"Описание изображения: {document_summary}. "
                            "Ответь по контексту и при необходимости задай один уточняющий вопрос."
                        )
                    else:
                        text_content = (
                            "Пользователь отправил файл без текста. "
                            "Попроси кратко уточнить запрос."
                        )
                elif message_kind in {"voice", "audio", "video"}:
                    transcript = await self.transcribe_media_message(
                        message_data, message_kind
                    )
                    if transcript:
                        text_content = (
                            f"Пользователь отправил {message_kind}. "
                            f"Расшифровка сообщения: {transcript}. "
                            "Ответь по контексту и задай один следующий вопрос по воронке."
                        )
                    else:
                        text_content = (
                            f"Пользователь отправил {message_kind} без текста. "
                            "Транскрипция недоступна, попроси кратко повторить ключевую мысль текстом."
                        )
                else:
                    text_content = (
                        f"Пользователь отправил {message_kind} без текста. "
                        "Попроси кратко уточнить запрос."
                    )

            sanitization_result = self.sanitizer.sanitize(text_content)

            if sanitization_result.violations:
                logger.warning(
                    "Input sanitization violations",
                    chat_id=chat_id,
                    violations=sanitization_result.violations,
                )

                # Для серьезных нарушений прекращаем обработку
                serious_violations = [
                    v
                    for v in sanitization_result.violations
                    if "prompt_injection" in v or "xss_attempt" in v
                ]

                if serious_violations:
                    await self.idempotency_manager.mark_completed(
                        "telegram_update", str(update.update_id), "security_violation"
                    )

                    return WebhookResponse(
                        status="blocked",
                        message="Message blocked due to security policy",
                        update_id=update.update_id,
                    )

            # Обработка через FSM и flow manager
            try:
                flow_response = await self.flow_manager.process_message(
                    user_info=user_info,
                    message_text=sanitization_result.sanitized_text,
                    message_data=message_data,
                    update=update.dict(),
                )

                processing_time = time.time() - start_time

                logger.info(
                    "Update processed successfully",
                    update_id=update.update_id,
                    chat_id=chat_id,
                    processing_time_ms=int(processing_time * 1000),
                    flow_state=flow_response.get("state"),
                    response_sent=flow_response.get("response_sent", False),
                )

                # Сохранение результата
                await self.idempotency_manager.mark_completed(
                    "telegram_update", str(update.update_id), flow_response
                )

                return WebhookResponse(
                    status="processed",
                    message="Update processed successfully",
                    update_id=update.update_id,
                )

            except Exception as flow_error:
                logger.error(
                    "Flow processing error",
                    update_id=update.update_id,
                    chat_id=chat_id,
                    error=str(flow_error),
                )

                # Помечаем как неуспешную обработку
                await self.idempotency_manager.mark_failed(
                    "telegram_update", str(update.update_id), str(flow_error)
                )

                raise flow_error

        except Exception as e:
            processing_time = time.time() - start_time

            # Увеличиваем счетчик ошибок
            self.metrics_collector.increment_counter("telegram_updates_errors_total")

            logger.error(
                "Update processing failed",
                update_id=update.update_id,
                error=str(e),
                processing_time_ms=int(processing_time * 1000),
                exc_info=True,
            )

            return WebhookResponse(
                status="error",
                message="Internal processing error",
                update_id=update.update_id,
            )


# Глобальный экземпляр процессора
_webhook_processor = TelegramWebhookProcessor()
_audio_monitor_last_status: Optional[str] = None
_audio_monitor_last_result: Dict[str, Any] = {}
_audio_monitor_last_checked_at: Optional[float] = None
_audio_monitor_last_alert_at: Optional[float] = None
_audio_monitor_last_recovery_at: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager для FastAPI приложения"""
    # Startup
    logger.info("Starting Telegram webhook service")
    await _webhook_processor.initialize()
    app.state.audio_selftest_stop_event = asyncio.Event()
    app.state.audio_selftest_task = None

    monitor_enabled = os.getenv("AUDIO_SELFTEST_MONITOR_ENABLED", "true").lower()
    if monitor_enabled in {"1", "true", "yes", "on"}:
        app.state.audio_selftest_task = asyncio.create_task(
            _audio_selftest_monitor_loop(app.state.audio_selftest_stop_event)
        )
        logger.info(
            "audio_selftest_monitor_started",
            interval_sec=int(os.getenv("AUDIO_SELFTEST_INTERVAL_SEC", "900")),
            quiet_enabled=os.getenv("AUDIO_SELFTEST_QUIET_ENABLED", "true"),
            quiet_start_hour=os.getenv("AUDIO_SELFTEST_QUIET_START_HOUR", "1"),
            quiet_end_hour=os.getenv("AUDIO_SELFTEST_QUIET_END_HOUR", "8"),
            quiet_tz=os.getenv("AUDIO_SELFTEST_QUIET_TZ", "Europe/Moscow"),
        )
    else:
        logger.info("audio_selftest_monitor_disabled")

    yield

    # Shutdown
    logger.info("Shutting down Telegram webhook service")
    try:
        if getattr(app.state, "audio_selftest_stop_event", None):
            app.state.audio_selftest_stop_event.set()
        if getattr(app.state, "audio_selftest_task", None):
            await asyncio.wait_for(app.state.audio_selftest_task, timeout=5)
    except asyncio.TimeoutError:
        logger.warning("audio_selftest_monitor_stop_timeout")
    except Exception as e:
        logger.warning("audio_selftest_monitor_stop_error", error=str(e))

    try:
        if _webhook_processor.llm_orchestrator:
            await _webhook_processor.llm_orchestrator.close()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# Создание FastAPI приложения
app = FastAPI(
    title="SoVAni AI Seller Telegram Webhook",
    description="Webhook для обработки Telegram updates с полным AI pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

# Настройка rate limiting middleware (если доступна)
if setup_middlewares is not None:
    try:
        redis_url = None
        try:
            redis_url = get_redis_url()
        except ValueError:
            logger.warning("redis_url_not_set_for_ratelimit")
        setup_middlewares(app, redis_url=redis_url)
        logger.info("ratelimit_middleware_setup_complete")
    except Exception as e:
        logger.warning("ratelimit_middleware_setup_failed", error=str(e))
else:
    logger.warning("ratelimit_disabled_fallback")

# CORS middleware - настроено для продакшена
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://saleswhisper.pro",
        "https://www.saleswhisper.pro",
        "http://localhost:3000",  # для локальной разработки
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Account-Id"],
)

# Register AmoCRM/Kommo routers
if AMOCRM_ROUTERS_AVAILABLE:
    if amocrm_oauth_router:
        app.include_router(amocrm_oauth_router, prefix="/api")
        logger.info("AmoCRM OAuth router registered at /api/amocrm")
    if widget_api_router:
        app.include_router(widget_api_router, prefix="/api")
        logger.info("Widget API v2 router registered at /api/widget")

# Register Licensing and Payment routers
if PAYMENT_ROUTERS_AVAILABLE:
    if licensing_router:
        app.include_router(licensing_router, prefix="/api")
        logger.info("Licensing router registered at /api/license")
    if payment_router:
        app.include_router(payment_router, prefix="/api")
        logger.info("Payment router registered at /api/payment")
else:
    logger.warning("AmoCRM routers not available - services not imported")


@app.get("/healthz")
async def health_check():
    """Health check эндпоинт"""
    return {"status": "healthy", "service": "telegram-webhook"}


@app.get("/health")
async def health_check_alias():
    """Алиас для /healthz для совместимости"""
    return {"status": "healthy", "service": "telegram-webhook"}


@app.get("/api/health")
async def api_health_check():
    """Алиас для внешнего мониторинга через /api/health."""
    return {"status": "healthy", "service": "telegram-webhook"}


class WebChatRequest(BaseModel):
    """Запрос веб-чата"""

    message: str
    session_id: Optional[str] = None


class WebChatResponse(BaseModel):
    """Ответ веб-чата"""

    response: str
    session_id: str
    status: str = "ok"


@app.post("/api/chat")
async def web_chat(request: WebChatRequest):
    """
    API эндпоинт для веб-чата.

    Обрабатывает сообщения из виджета на сайте через AI Seller.
    """
    import uuid
    from dialog.two_layer_flow_manager import get_two_layer_flow_manager

    # Генерируем session_id если не передан
    session_id = request.session_id or f"web_{uuid.uuid4().hex[:16]}"

    logger.info(
        f"Web chat request: session={session_id}, message='{request.message[:50]}...'"
    )

    try:
        # Получаем flow manager
        flow_manager = get_two_layer_flow_manager()

        # Обрабатываем сообщение
        result = await flow_manager.process_web_message(
            session_id=session_id, message_text=request.message
        )

        return WebChatResponse(
            response=result.get("response", ""),
            session_id=session_id,
            status=result.get("status", "ok"),
        )

    except Exception as e:
        logger.error(f"Web chat error: {e}", exc_info=True)
        return WebChatResponse(
            response="Произошла ошибка. Попробуйте позже или напишите нам в Telegram.",
            session_id=session_id,
            status="error",
        )


# ============================================
# Leads API - Единое хранилище заявок
# ============================================


class LeadSubmitRequest(BaseModel):
    """Запрос на создание заявки"""

    name: str
    contact: str  # телефон, email или telegram
    message: Optional[str] = None
    source: str = "website_form"
    niche: Optional[str] = None
    session_id: Optional[str] = None


class LeadResponse(BaseModel):
    """Ответ API заявок"""

    status: str
    lead_id: Optional[int] = None
    message: Optional[str] = None


class AudioSelftestRequest(BaseModel):
    """Запрос на self-test аудио цепочки"""

    api_key: Optional[str] = None
    run_tts: bool = True
    run_stt: bool = True
    test_text: str = "Проверка аудио цепочки SalesWhisper."
    tts_voice: str = "nova"


class AudioSelftestResponse(BaseModel):
    """Ответ self-test аудио цепочки"""

    status: str
    tts_ok: bool = False
    stt_ok: bool = False
    tts_bytes: int = 0
    stt_text: str = ""
    details: Dict[str, Any] = {}


async def _send_audio_monitor_alert(message: str, force: bool = False) -> None:
    """Отправить алерт по аудио self-test в Telegram."""
    bot_token = (
        os.getenv("MONITORING_BOT_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )
    chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")

    if not bot_token or not chat_id:
        return

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            await s.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_notification": not force,
                },
            )
    except Exception as e:
        logger.warning("audio_monitor_alert_send_failed", error=str(e))


async def _run_audio_selftest_internal(
    run_tts: bool = True,
    run_stt: bool = True,
    test_text: str = "Проверка аудио цепочки SalesWhisper.",
    tts_voice: str = "nova",
) -> AudioSelftestResponse:
    """Внутренний self-test аудио цепочки без проверки API-ключа endpoint."""
    details: Dict[str, Any] = {
        "tts_model": os.getenv("TTS_MODEL", "gpt-4o-mini-tts"),
        "tts_fallback_model": os.getenv("TTS_FALLBACK_MODEL", "tts-1-hd"),
        "stt_model": os.getenv("STT_MODEL", "gpt-4o-mini-transcribe"),
        "stt_fallback_model": os.getenv("STT_FALLBACK_MODEL", "whisper-1"),
    }

    tts_ok = False
    stt_ok = False
    tts_audio: bytes = b""
    stt_text = ""

    try:
        if run_tts:
            from audio.text_to_speech import create_tts_service

            tts_service = await create_tts_service("openai")
            tts_audio = await tts_service.synthesize_text(test_text, voice=tts_voice)
            tts_ok = bool(tts_audio)
            details["tts_voice"] = tts_voice

        if run_stt:
            if not tts_audio:
                details["stt_skipped_reason"] = "No audio source for STT check"
            else:
                from audio.speech_to_text import create_stt_service

                stt_service = create_stt_service("openai")
                stt_text = await stt_service.provider.transcribe(tts_audio, "mp3")
                stt_ok = bool(stt_text and stt_text.strip())

        status = (
            "ok"
            if (not run_tts or tts_ok) and (not run_stt or stt_ok)
            else "degraded"
        )
        return AudioSelftestResponse(
            status=status,
            tts_ok=tts_ok,
            stt_ok=stt_ok,
            tts_bytes=len(tts_audio),
            stt_text=(stt_text[:300] if stt_text else ""),
            details=details,
        )

    except Exception as e:
        logger.error("audio_selftest_error", error=str(e), exc_info=True)
        details["error"] = str(e)
        return AudioSelftestResponse(
            status="error",
            tts_ok=tts_ok,
            stt_ok=stt_ok,
            tts_bytes=len(tts_audio),
            stt_text=(stt_text[:300] if stt_text else ""),
            details=details,
        )


async def _audio_selftest_monitor_loop(stop_event: asyncio.Event) -> None:
    """Фоновый цикл мониторинга аудио self-test."""
    global _audio_monitor_last_status
    global _audio_monitor_last_result
    global _audio_monitor_last_checked_at
    global _audio_monitor_last_alert_at
    global _audio_monitor_last_recovery_at

    interval = int(os.getenv("AUDIO_SELFTEST_INTERVAL_SEC", "900"))
    test_text = os.getenv(
        "AUDIO_SELFTEST_TEXT", "Проверка аудио цепочки SalesWhisper мониторинг."
    )
    tts_voice = os.getenv("AUDIO_SELFTEST_VOICE", "nova")
    quiet_enabled = os.getenv("AUDIO_SELFTEST_QUIET_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    quiet_start = int(os.getenv("AUDIO_SELFTEST_QUIET_START_HOUR", "1"))
    quiet_end = int(os.getenv("AUDIO_SELFTEST_QUIET_END_HOUR", "8"))
    quiet_tz_name = os.getenv("AUDIO_SELFTEST_QUIET_TZ", "Europe/Moscow")

    def is_quiet_hours_now() -> bool:
        if not quiet_enabled:
            return False
        try:
            hour = datetime.now(ZoneInfo(quiet_tz_name)).hour
        except Exception:
            hour = datetime.utcnow().hour

        # Поддержка диапазонов через полночь
        if quiet_start < quiet_end:
            return quiet_start <= hour < quiet_end
        return hour >= quiet_start or hour < quiet_end

    while not stop_event.is_set():
        result = await _run_audio_selftest_internal(
            run_tts=True,
            run_stt=True,
            test_text=test_text,
            tts_voice=tts_voice,
        )
        _audio_monitor_last_checked_at = time.time()
        _audio_monitor_last_result = result.dict()
        current_status = result.status
        previous_status = _audio_monitor_last_status

        if current_status != "ok" and previous_status != current_status:
            error_text = result.details.get("error", "unknown")
            msg = (
                "ALERT: audio selftest failed\n"
                f"status={current_status}\n"
                f"tts_ok={result.tts_ok} stt_ok={result.stt_ok}\n"
                f"error={error_text}"
            )
            if is_quiet_hours_now():
                logger.warning(
                    "audio_selftest_monitor_alert_suppressed_quiet_hours",
                    status=current_status,
                )
            else:
                await _send_audio_monitor_alert(msg, force=True)
                _audio_monitor_last_alert_at = time.time()
            logger.warning("audio_selftest_monitor_alert", status=current_status)

        if current_status == "ok" and previous_status in {"degraded", "error"}:
            msg = (
                "RECOVERY: audio selftest restored\n"
                f"tts_ok={result.tts_ok} stt_ok={result.stt_ok}\n"
                f"tts_bytes={result.tts_bytes}"
            )
            if is_quiet_hours_now():
                logger.info("audio_selftest_recovery_suppressed_quiet_hours")
            else:
                await _send_audio_monitor_alert(msg, force=False)
                _audio_monitor_last_recovery_at = time.time()
            logger.info("audio_selftest_monitor_recovered")

        _audio_monitor_last_status = current_status

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


@app.post("/api/leads")
async def submit_lead(request: LeadSubmitRequest):
    """
    API для создания заявки.

    Принимает заявки с сайта, виджета, формы.
    """
    from services.leads_storage import get_leads_storage, Lead, detect_contact_type

    logger.info(f"Lead submission: source={request.source}, name={request.name}")

    try:
        storage = await get_leads_storage()

        # Определяем тип контакта
        contact_type = detect_contact_type(request.contact)

        # Создаём заявку
        lead = Lead(
            id=0,  # будет присвоен автоматически
            source=request.source,
            name=request.name,
            contact=request.contact,
            contact_type=contact_type,
            niche=request.niche,
            message=request.message,
            session_id=request.session_id,
        )

        result = await storage.save_lead(lead)

        if result["status"] == "success":
            return LeadResponse(
                status="success",
                lead_id=result["lead_id"],
                message="Заявка успешно отправлена! Мы свяжемся с вами в ближайшее время.",
            )
        else:
            return LeadResponse(
                status="error", message="Произошла ошибка при сохранении заявки."
            )

    except Exception as e:
        logger.error(f"Lead submission error: {e}", exc_info=True)
        return LeadResponse(
            status="error", message="Произошла ошибка. Попробуйте позже."
        )


def _require_api_key(provided_key: Optional[str], env_name: str) -> None:
    """Единая fail-closed проверка API ключей."""
    expected_key = os.getenv(env_name)
    if not expected_key:
        logger.error(f"{env_name} not configured")
        raise HTTPException(status_code=503, detail="API key is not configured")

    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/api/leads")
async def get_leads(
    source: Optional[str] = None, limit: int = 100, api_key: Optional[str] = None
):
    """
    Получить список заявок (требует API ключ).
    """
    from services.leads_storage import get_leads_storage

    _require_api_key(api_key, "LEADS_API_KEY")

    try:
        storage = await get_leads_storage()
        leads = await storage.get_leads(source=source, limit=limit)

        return {"status": "success", "count": len(leads), "leads": leads}

    except Exception as e:
        logger.error(f"Get leads error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


@app.get("/api/leads/stats")
async def get_leads_stats(api_key: Optional[str] = None):
    """
    Статистика по заявкам (требует API ключ).
    """
    from services.leads_storage import get_leads_storage

    _require_api_key(api_key, "LEADS_API_KEY")

    try:
        storage = await get_leads_storage()
        stats = await storage.get_stats()

        return {"status": "success", "stats": stats}

    except Exception as e:
        logger.error(f"Get stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


@app.post("/api/audio/selftest", response_model=AudioSelftestResponse)
async def audio_selftest(request: AudioSelftestRequest):
    """
    Self-test аудио цепочки (TTS/STT).

    Требует LEADS_API_KEY.
    """
    _require_api_key(request.api_key, "LEADS_API_KEY")
    return await _run_audio_selftest_internal(
        run_tts=request.run_tts,
        run_stt=request.run_stt,
        test_text=request.test_text,
        tts_voice=request.tts_voice,
    )


@app.get("/api/audio/monitor/status")
async def audio_monitor_status(api_key: Optional[str] = None):
    """
    Статус фонового аудио-монитора и последнего self-test.

    Требует LEADS_API_KEY.
    """
    _require_api_key(api_key, "LEADS_API_KEY")

    return {
        "status": "ok",
        "monitor": {
            "enabled": os.getenv("AUDIO_SELFTEST_MONITOR_ENABLED", "true"),
            "interval_sec": int(os.getenv("AUDIO_SELFTEST_INTERVAL_SEC", "900")),
            "voice": os.getenv("AUDIO_SELFTEST_VOICE", "nova"),
            "quiet_enabled": os.getenv("AUDIO_SELFTEST_QUIET_ENABLED", "true"),
            "quiet_start_hour": int(
                os.getenv("AUDIO_SELFTEST_QUIET_START_HOUR", "1")
            ),
            "quiet_end_hour": int(os.getenv("AUDIO_SELFTEST_QUIET_END_HOUR", "8")),
            "quiet_tz": os.getenv("AUDIO_SELFTEST_QUIET_TZ", "Europe/Moscow"),
        },
        "last": {
            "status": _audio_monitor_last_status,
            "checked_at": _audio_monitor_last_checked_at,
            "alert_at": _audio_monitor_last_alert_at,
            "recovery_at": _audio_monitor_last_recovery_at,
            "result": _audio_monitor_last_result,
        },
    }


@app.get("/api/audio/monitor/ping")
async def audio_monitor_ping(
    request: Request, api_key: Optional[str] = None, no_key: bool = False
):
    """
    Короткий статус фонового аудио-монитора для external uptime checks.

    Требует LEADS_API_KEY.
    """
    allow_local_no_key = os.getenv("AUDIO_MONITOR_PING_ALLOW_LOCAL_NO_KEY", "true")
    allow_local_no_key = allow_local_no_key.lower() in {"1", "true", "yes", "on"}
    client_host = request.client.host if request.client else ""

    if no_key and allow_local_no_key and client_host in {"127.0.0.1", "::1", "localhost"}:
        pass
    else:
        _require_api_key(api_key, "LEADS_API_KEY")

    monitor_status = _audio_monitor_last_status or "unknown"
    http_status = "ok" if monitor_status == "ok" else "degraded"

    return {
        "status": http_status,
        "monitor_status": monitor_status,
        "checked_at": _audio_monitor_last_checked_at,
    }


@app.get("/readyz")
async def readiness_check():
    """Readiness check эндпоинт с полной проверкой здоровья"""
    try:
        health_checker = await get_health_checker()
        health_result = await health_checker.run_full_health_check()

        # Определение HTTP статуса на основе общего состояния
        status_code = 200
        if health_result["status"] == "degraded":
            status_code = 200  # Сервис работает, но есть предупреждения
        elif health_result["status"] == "unhealthy":
            status_code = 503  # Service Unavailable

        return JSONResponse(content=health_result, status_code=status_code)

    except Exception as e:
        logger.error(f"Health check error: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Health check failed: {str(e)}",
                "timestamp": time.time(),
            },
            status_code=500,
        )


async def handle_update(request: Request) -> Dict[str, Any]:
    """
    Общая логика обработки Telegram update
    """
    # Получение raw body для валидации
    body = await request.body()

    # Парсинг JSON
    try:
        update_data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in webhook request: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Валидация структуры update
    try:
        update = TelegramUpdate(**update_data)
    except Exception as e:
        logger.warning(f"Invalid Telegram update structure: {e}")
        raise HTTPException(status_code=400, detail="Invalid update structure")

    # Обработка update
    response = await _webhook_processor.process_update(update)

    return response


@app.post(f"/telegram/{os.getenv('WEBHOOK_SECRET_PATH', 'SECRET')}")
async def telegram_webhook(request: Request):
    """
    Основной Telegram webhook эндпоинт

    Path содержит секретный токен для безопасности
    """
    try:
        return await handle_update(request)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ПРОДАКШЕН: Стандартный маршрут с проверкой заголовка X-Telegram-Bot-Api-Secret-Token
@app.post("/telegram/webhook")
async def telegram_webhook_standard(request: Request):
    """
    Стандартный Telegram webhook с проверкой X-Telegram-Bot-Api-Secret-Token

    Проверяет заголовок X-Telegram-Bot-Api-Secret-Token на точное совпадение
    с TELEGRAM_WEBHOOK_SECRET из переменных окружения.
    """
    # Получение секретного токена из заголовка
    received_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    expected_token = os.getenv("TELEGRAM_WEBHOOK_SECRET")

    # Проверка наличия обоих токенов
    if not expected_token:
        logger.error("TELEGRAM_WEBHOOK_SECRET not configured")
        return Response(status_code=404)

    if not received_token:
        logger.warning("Missing X-Telegram-Bot-Api-Secret-Token header")
        return Response(status_code=404)

    # Безопасное сравнение токенов
    if not hmac.compare_digest(received_token, expected_token):
        logger.warning(
            "Invalid X-Telegram-Bot-Api-Secret-Token",
            received_length=len(received_token),
            expected_length=len(expected_token),
        )
        return Response(status_code=404)

    try:
        return await handle_update(request)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# HOTFIX: Алиасный маршрут с HMAC проверкой секрета
@app.post("/telegram/{secret}")
async def telegram_secret_webhook(secret: str, request: Request):
    """
    Алиасный Telegram webhook с динамической проверкой секрета

    HOTFIX: Для совместимости с различными настройками webhook
    """
    allow_alias = (
        os.getenv("TELEGRAM_WEBHOOK_ALLOW_SECRET_PATH_ALIAS", "false").lower()
        == "true"
    )
    if not allow_alias:
        return Response(status_code=404)

    # Проверка секрета через HMAC
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not webhook_secret:
        return Response(status_code=404)
    if not hmac.compare_digest(secret, webhook_secret):
        return Response(status_code=404)

    try:
        return await handle_update(request)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus метрики эндпоинт"""
    # Базовая HTTP аутентификация для продакшена
    auth_header = request.headers.get("Authorization")
    metrics_auth = os.getenv("METRICS_BASIC_AUTH")

    if metrics_auth and auth_header != f"Basic {metrics_auth}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        metrics_collector = await get_metrics_collector()

        # Обновление системных и приложений метрик
        await metrics_collector.collect_system_metrics()
        await metrics_collector.collect_application_metrics()

        # Возврат в Prometheus формате
        accept_header = request.headers.get("Accept", "")
        if "application/json" in accept_header:
            return metrics_collector.get_metrics_json()
        else:
            # Prometheus text format
            prometheus_text = metrics_collector.format_prometheus_metrics()
            return Response(content=prometheus_text, media_type="text/plain")

    except Exception as e:
        logger.error(f"Metrics collection error: {e}")
        raise HTTPException(status_code=500, detail="Metrics collection failed")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Обработчик 404 ошибок"""
    return JSONResponse(
        status_code=404, content={"error": "Not found", "path": str(request.url.path)}
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    """Обработчик 500 ошибок"""
    logger.error(f"Internal server error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


if __name__ == "__main__":
    # Запуск для разработки/продакшена с оптимизациями
    port = int(os.getenv("WEBHOOK_PORT", "8000"))
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    workers = int(os.getenv("UVICORN_WORKERS", "1"))

    # Оптимизация для продакшена
    use_uvloop = os.getenv("USE_UVLOOP", "true").lower() == "true"

    uvicorn_config = {
        "host": host,
        "port": port,
        "workers": workers,
        "log_config": None,  # Используем наш логгер
        "access_log": False,  # Отключаем access логи для производительности
        "reload": os.getenv("APP_ENV", "production") != "production",
    }

    # Дополнительные оптимизации для продакшена
    if os.getenv("APP_ENV", "production") == "production":
        uvicorn_config.update(
            {
                "loop": "uvloop" if use_uvloop else "asyncio",
                "http": "httptools",
                "lifespan": "on",
                "timeout_keep_alive": 30,
                "timeout_notify": 30,
                "limit_max_requests": 10000,
                "limit_concurrency": 1000,
            }
        )

        logger.info(
            f"Starting production server: host={host}, port={port}, workers={workers}"
        )
        logger.info("Performance optimizations: loop=uvloop, http=httptools")
    else:
        logger.info(f"Starting development server: host={host}, port={port}")

    uvicorn.run("webhook:app", **uvicorn_config)
