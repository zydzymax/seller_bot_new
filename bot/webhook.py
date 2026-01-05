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
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка uvloop для повышения производительности
try:
    import uvloop
    uvloop.install()
    print("uvloop installed for enhanced performance")
except ImportError:
    print("uvloop not available, using default asyncio loop")

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import hmac
import uvicorn
from pydantic import BaseModel

from utils.logging import get_logger, set_context
from utils.rate_limit import get_rate_limiter, RateLimitMiddleware
from utils.idempotency import get_idempotency_manager, IdempotencyMiddleware
from utils.input_sanitizer import get_sanitizer
from utils.metrics import get_metrics_collector, get_health_checker, track_duration
from llm.orchestrator import get_orchestrator
from dialog.flow_router import get_flow_manager  # Router to select flow manager by domain
from adapters.crm_adapter import get_crm_adapter

# AmoCRM/Kommo OAuth and Widget API
try:
    from services.amocrm_oauth import router as amocrm_oauth_router
    from services.widget_api_v2 import router as widget_api_router
try:
    from services.licensing import router as licensing_router
    from services.payment import router as payment_router
    PAYMENT_ROUTERS_AVAILABLE = True
except ImportError as e:
    PAYMENT_ROUTERS_AVAILABLE = False
    licensing_router = None
    payment_router = None
    AMOCRM_ROUTERS_AVAILABLE = True
except ImportError as e:
    AMOCRM_ROUTERS_AVAILABLE = False
    amocrm_oauth_router = None
    widget_api_router = None

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
    processed_at: float = time.time()


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
        elif update.callback_query and update.callback_query.get('message'):
            message_data = update.callback_query['message']
            message_data['callback_data'] = update.callback_query.get('data')
            
        return message_data
        
    def extract_user_info(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Извлечение информации о пользователе"""
        from_user = message_data.get('from', {})
        chat = message_data.get('chat', {})
        
        return {
            'user_id': from_user.get('id'),
            'username': from_user.get('username'),
            'first_name': from_user.get('first_name'),
            'last_name': from_user.get('last_name'),
            'language_code': from_user.get('language_code', 'ru'),
            'chat_id': chat.get('id'),
            'chat_type': chat.get('type'),
            'is_bot': from_user.get('is_bot', False)
        }
        
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
            set_context(
                update_id=update.update_id,
                component="telegram_webhook"
            )
            
            logger.info("Processing Telegram update", update_id=update.update_id)
            
            # Проверка идемпотентности
            idem_result = await self.idempotency_manager.check_duplicate(
                "telegram_update", 
                str(update.update_id)
            )
            
            if idem_result.is_duplicate:
                logger.info("Duplicate update ignored", update_id=update.update_id)
                self.metrics_collector.increment_counter("idempotency_duplicates_total")
                return WebhookResponse(
                    status="duplicate",
                    message="Update already processed",
                    update_id=update.update_id
                )
            
            # Отметка начала обработки
            if not await self.idempotency_manager.mark_processing(
                "telegram_update",
                str(update.update_id),
                ttl=300  # 5 минут TTL для обработки
            ):
                logger.warning("Update processing race condition", update_id=update.update_id)
                return WebhookResponse(
                    status="processing",
                    message="Update is being processed",
                    update_id=update.update_id
                )
                
            # Извлечение данных сообщения
            message_data = self.extract_message_data(update)
            
            if not message_data:
                logger.info("No processable message in update", update_id=update.update_id)
                await self.idempotency_manager.mark_completed(
                    "telegram_update",
                    str(update.update_id),
                    "no_message"
                )
                return WebhookResponse(
                    status="ignored",
                    message="No processable message",
                    update_id=update.update_id
                )
                
            # Извлечение информации о пользователе
            user_info = self.extract_user_info(message_data)
            chat_id = user_info['chat_id']
            
            # Обновление контекста логирования
            set_context(
                update_id=update.update_id,
                chat_id=chat_id,
                user_id=user_info['user_id'],
                component="telegram_webhook"
            )
            
            # Проверка rate limiting по chat_id
            allowed, rate_metadata = await self.rate_limiter.is_allowed(
                str(chat_id), "chat"
            )
            
            if not allowed:
                logger.warning("Rate limit exceeded for chat", 
                              chat_id=chat_id,
                              retry_after=rate_metadata.get('retry_after'))
                
                self.metrics_collector.increment_counter("rate_limit_blocked_total")
                              
                # Все равно помечаем как обработанный чтобы избежать повторов
                await self.idempotency_manager.mark_completed(
                    "telegram_update",
                    str(update.update_id),
                    "rate_limited"
                )
                
                return WebhookResponse(
                    status="rate_limited",
                    message=f"Too many requests. Try again in {rate_metadata.get('retry_after', 60)} seconds.",
                    update_id=update.update_id
                )
                
            # Извлечение и санитизация текста сообщения
            text_content = message_data.get('text', '')
            if update.callback_query:
                text_content = message_data.get('callback_data', '')
                
            sanitization_result = self.sanitizer.sanitize(text_content)
            
            if sanitization_result.violations:
                logger.warning("Input sanitization violations",
                              chat_id=chat_id,
                              violations=sanitization_result.violations)
                              
                # Для серьезных нарушений прекращаем обработку
                serious_violations = [
                    v for v in sanitization_result.violations 
                    if 'prompt_injection' in v or 'xss_attempt' in v
                ]
                
                if serious_violations:
                    await self.idempotency_manager.mark_completed(
                        "telegram_update",
                        str(update.update_id),
                        "security_violation"
                    )
                    
                    return WebhookResponse(
                        status="blocked",
                        message="Message blocked due to security policy",
                        update_id=update.update_id
                    )
                    
            # Обработка через FSM и flow manager
            try:
                flow_response = await self.flow_manager.process_message(
                    user_info=user_info,
                    message_text=sanitization_result.sanitized_text,
                    message_data=message_data,
                    update=update.dict()
                )
                
                processing_time = time.time() - start_time
                
                logger.info("Update processed successfully",
                           update_id=update.update_id,
                           chat_id=chat_id,
                           processing_time_ms=int(processing_time * 1000),
                           flow_state=flow_response.get('state'),
                           response_sent=flow_response.get('response_sent', False))
                           
                # Сохранение результата
                await self.idempotency_manager.mark_completed(
                    "telegram_update",
                    str(update.update_id),
                    flow_response
                )
                
                return WebhookResponse(
                    status="processed",
                    message="Update processed successfully",
                    update_id=update.update_id
                )
                
            except Exception as flow_error:
                logger.error("Flow processing error",
                           update_id=update.update_id,
                           chat_id=chat_id,
                           error=str(flow_error))
                           
                # Помечаем как неуспешную обработку
                await self.idempotency_manager.mark_failed(
                    "telegram_update",
                    str(update.update_id),
                    str(flow_error)
                )
                
                raise flow_error
                
        except Exception as e:
            processing_time = time.time() - start_time
            
            # Увеличиваем счетчик ошибок
            self.metrics_collector.increment_counter("telegram_updates_errors_total")
            
            logger.error("Update processing failed",
                        update_id=update.update_id,
                        error=str(e),
                        processing_time_ms=int(processing_time * 1000),
                        exc_info=True)
                        
            return WebhookResponse(
                status="error",
                message="Internal processing error",
                update_id=update.update_id
            )


# Глобальный экземпляр процессора
_webhook_processor = TelegramWebhookProcessor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager для FastAPI приложения"""
    # Startup
    logger.info("Starting Telegram webhook service")
    await _webhook_processor.initialize()
    
    yield
    
    # Shutdown
    logger.info("Shutting down Telegram webhook service")
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
    lifespan=lifespan
)

# Настройка rate limiting middleware (если доступна)
if setup_middlewares is not None:
    try:
        setup_middlewares(app, redis_url=os.getenv("REDIS_URL"))
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

    logger.info(f"Web chat request: session={session_id}, message='{request.message[:50]}...'")

    try:
        # Получаем flow manager
        flow_manager = get_two_layer_flow_manager()

        # Обрабатываем сообщение
        result = await flow_manager.process_web_message(
            session_id=session_id,
            message_text=request.message
        )

        return WebChatResponse(
            response=result.get("response", ""),
            session_id=session_id,
            status=result.get("status", "ok")
        )

    except Exception as e:
        logger.error(f"Web chat error: {e}", exc_info=True)
        return WebChatResponse(
            response="Произошла ошибка. Попробуйте позже или напишите нам в Telegram.",
            session_id=session_id,
            status="error"
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
            session_id=request.session_id
        )

        result = await storage.save_lead(lead)

        if result["status"] == "success":
            return LeadResponse(
                status="success",
                lead_id=result["lead_id"],
                message="Заявка успешно отправлена! Мы свяжемся с вами в ближайшее время."
            )
        else:
            return LeadResponse(
                status="error",
                message="Произошла ошибка при сохранении заявки."
            )

    except Exception as e:
        logger.error(f"Lead submission error: {e}", exc_info=True)
        return LeadResponse(
            status="error",
            message="Произошла ошибка. Попробуйте позже."
        )


@app.get("/api/leads")
async def get_leads(
    source: Optional[str] = None,
    limit: int = 100,
    api_key: Optional[str] = None
):
    """
    Получить список заявок (требует API ключ).
    """
    from services.leads_storage import get_leads_storage

    # Простая проверка API ключа
    expected_key = os.getenv("LEADS_API_KEY", "saleswhisper_admin_2025")
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        storage = await get_leads_storage()
        leads = await storage.get_leads(source=source, limit=limit)

        return {
            "status": "success",
            "count": len(leads),
            "leads": leads
        }

    except Exception as e:
        logger.error(f"Get leads error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


@app.get("/api/leads/stats")
async def get_leads_stats(api_key: Optional[str] = None):
    """
    Статистика по заявкам (требует API ключ).
    """
    from services.leads_storage import get_leads_storage

    expected_key = os.getenv("LEADS_API_KEY", "saleswhisper_admin_2025")
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        storage = await get_leads_storage()
        stats = await storage.get_stats()

        return {
            "status": "success",
            "stats": stats
        }

    except Exception as e:
        logger.error(f"Get stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


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
                "timestamp": time.time()
            },
            status_code=500
        )


async def handle_update(request: Request) -> Dict[str, Any]:
    """
    Общая логика обработки Telegram update
    """
    # Получение raw body для валидации
    body = await request.body()
    
    # Парсинг JSON
    try:
        update_data = json.loads(body.decode('utf-8'))
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
        logger.warning("Invalid X-Telegram-Bot-Api-Secret-Token", 
                      received_length=len(received_token),
                      expected_length=len(expected_token))
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
    # Проверка секрета через HMAC
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if webhook_secret and not hmac.compare_digest(secret, webhook_secret):
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
    auth_header = request.headers.get('Authorization')
    metrics_auth = os.getenv('METRICS_BASIC_AUTH')
    
    if metrics_auth and auth_header != f'Basic {metrics_auth}':
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        metrics_collector = await get_metrics_collector()
        
        # Обновление системных и приложений метрик
        await metrics_collector.collect_system_metrics()
        await metrics_collector.collect_application_metrics()
        
        # Возврат в Prometheus формате
        accept_header = request.headers.get('Accept', '')
        if 'application/json' in accept_header:
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
        status_code=404,
        content={"error": "Not found", "path": str(request.url.path)}
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    """Обработчик 500 ошибок"""
    logger.error(f"Internal server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


if __name__ == "__main__":
    # Запуск для разработки/продакшена с оптимизациями
    port = int(os.getenv('WEBHOOK_PORT', '8000'))
    host = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    workers = int(os.getenv('UVICORN_WORKERS', '1'))
    
    # Оптимизация для продакшена
    use_uvloop = os.getenv('USE_UVLOOP', 'true').lower() == 'true'
    
    uvicorn_config = {
        "host": host,
        "port": port,
        "workers": workers,
        "log_config": None,  # Используем наш логгер
        "access_log": False,  # Отключаем access логи для производительности
        "reload": os.getenv('APP_ENV', 'production') != 'production'
    }
    
    # Дополнительные оптимизации для продакшена
    if os.getenv('APP_ENV', 'production') == 'production':
        uvicorn_config.update({
            "loop": "uvloop" if use_uvloop else "asyncio",
            "http": "httptools",
            "lifespan": "on",
            "timeout_keep_alive": 30,
            "timeout_notify": 30,
            "limit_max_requests": 10000,
            "limit_concurrency": 1000
        })
        
        logger.info(f"Starting production server: host={host}, port={port}, workers={workers}")
        logger.info(f"Performance optimizations: loop=uvloop, http=httptools")
    else:
        logger.info(f"Starting development server: host={host}, port={port}")
    
    uvicorn.run("webhook:app", **uvicorn_config)