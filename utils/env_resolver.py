"""
env_resolver.py — Централизованный резолвер переменных окружения для SoVAni
Обеспечивает совместимость различных именований переменных окружения.
"""

import os
import logging

logger = logging.getLogger(__name__)

def get_telegram_token() -> str:
    """Получение Telegram токена с приоритетом TELEGRAM_BOT_TOKEN"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    
    token = os.getenv("TELEGRAM_TOKEN")
    if token:
        logger.warning("Using TELEGRAM_TOKEN (deprecated), prefer TELEGRAM_BOT_TOKEN")
        return token
    
    logger.error("No Telegram token found in environment")
    raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN must be set")

def get_redis_url() -> str:
    """Получение Redis URL с приоритетом REDIS_URL"""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url
    
    redis_addr = os.getenv("REDIS_ADDR")
    if redis_addr:
        logger.warning("Using REDIS_ADDR (deprecated), prefer REDIS_URL") 
        return redis_addr
    
    logger.error("No Redis URL found in environment")
    raise ValueError("REDIS_URL or REDIS_ADDR must be set")

def get_postgres_dsn() -> str:
    """Получение PostgreSQL DSN"""
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        logger.warning("POSTGRES_DSN not set")
        return None
    return dsn

def is_prod() -> bool:
    """Проверка production окружения"""
    app_env = os.getenv("APP_ENV", "").lower()
    return app_env in {"prod", "production"}

def get_webhook_secret() -> str:
    """Получение секрета для webhook"""
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        logger.warning("TELEGRAM_WEBHOOK_SECRET not set")
        return None
    return secret