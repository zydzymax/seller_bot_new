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

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel

from utils.logging import get_logger, set_context
from utils.rate_limit import get_rate_limiter, RateLimitMiddleware
from utils.idempotency import get_idempotency_manager, IdempotencyMiddleware
from utils.input_sanitizer import get_sanitizer
from utils.metrics import get_metrics_collector, get_health_checker, track_duration
from llm.orchestrator import get_orchestrator
from dialog.flow_manager import get_flow_manager
from adapters.crm_adapter import get_crm_adapter

logger = get_logger(__name__)


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

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене ограничить
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def health_check():
    """Health check эндпоинт"""
    return {"status": "healthy", "service": "telegram-webhook"}
    
    
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


@app.post(f"/telegram/{os.getenv('WEBHOOK_SECRET_PATH', 'SECRET')}")
async def telegram_webhook(request: Request):
    """
    Основной Telegram webhook эндпоинт
    
    Path содержит секретный токен для безопасности
    """
    try:
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
    # Запуск для разработки
    port = int(os.getenv('WEBHOOK_PORT', '8000'))
    host = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    
    uvicorn.run(
        "webhook:app",
        host=host,
        port=port,
        reload=os.getenv('APP_ENV', 'production') != 'production',
        log_config=None  # Используем наш логгер
    )