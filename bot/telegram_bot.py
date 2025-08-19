"""
telegram_bot.py — Telegram бот SoVAni с health-check и graceful shutdown

- Запуск через polling (локальный тест)
- Добавлен HTTP сервер для health-check
- DI: flow_manager, antiflood, sanitizer
- Чистый asyncio, без конфликтов event loop
"""

import os
import sys
import asyncio
import structlog

# --- Решение проблем с event loop ---
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telegram.ext import Application
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

from bot.handlers import setup_handlers
from utils.antiflood import AntiFloodMiddleware
from utils.input_sanitizer import sanitize_input
from dialog.flow_manager import FlowManager

logger = structlog.get_logger("ai_seller.telegram_bot")

def load_config():
    config = {}
    config["TELEGRAM_TOKEN"] = os.getenv("TELEGRAM_TOKEN")
    config["REDIS_URL"] = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    config["HEALTHCHECK_PORT"] = int(os.getenv("HEALTHCHECK_PORT", 8082))
    return config

async def healthcheck_handler(request):
    return web.Response(text="OK")

async def start_healthcheck_server(port):
    app = web.Application()
    app.router.add_get('/health', healthcheck_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info("healthcheck_started", port=port)
    return runner

async def import_redis_client(redis_url: str):
    import redis.asyncio as redis
    return redis.from_url(redis_url)

async def main():
    config = load_config()
    print(f"TELEGRAM_TOKEN = '{config['TELEGRAM_TOKEN']}'")

    # Увеличиваем timeout для стабильности
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(connection_pool_size=1, read_timeout=30, write_timeout=30, connect_timeout=10)
    application = Application.builder().token(config["TELEGRAM_TOKEN"]).request(request).build()

    redis_client = await import_redis_client(config["REDIS_URL"])
    flow_manager = FlowManager(redis_url=config["REDIS_URL"])
    antiflood = AntiFloodMiddleware(redis=redis_client, rate_limit=3, interval_sec=10)

    setup_handlers(application, flow_manager, sanitize_input, antiflood)

    healthcheck_runner = await start_healthcheck_server(config["HEALTHCHECK_PORT"])

    logger.info("polling_start")

    try:
        await application.run_polling()
    finally:
        await healthcheck_runner.cleanup()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
