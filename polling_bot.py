#!/usr/bin/env python3
"""
Telegram polling bot для SoVAni AI-продавца.
Получает сообщения через getUpdates и отправляет в webhook для обработки.
"""

import asyncio
import httpx
import os
import sys
from pathlib import Path
import redis.asyncio as redis

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from utils.logging import get_logger
from utils.env_resolver import is_prod

# Guard: запрет polling в production
if is_prod():
    logger = get_logger(__name__)
    logger.error("polling_forbidden_in_prod")
    sys.exit(1)

logger = get_logger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL = "http://localhost:8000/telegram/SECRET"


class PollingBot:
    """Polling бот, который пересылает сообщения в webhook"""

    def __init__(self):
        if not TELEGRAM_TOKEN:
            raise RuntimeError("TELEGRAM_TOKEN is required for polling mode")
        self.last_update_id = 0
        self.running = True
        self.redis_client = redis.from_url(
            os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
        )

    async def initialize_offset(self):
        """Инициализация offset для избежания конфликтов"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                # Получаем последние updates без timeout
                response = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"limit": 100},
                )

                if response.status_code == 200:
                    result = response.json()
                    updates = result.get("result", [])
                    if updates:
                        # Устанавливаем offset на последний update_id + 1
                        self.last_update_id = updates[-1]["update_id"]
                        logger.info(f"📍 Установлен offset: {self.last_update_id}")

                        # Подтверждаем получение всех updates
                        await client.get(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                            params={"offset": self.last_update_id + 1, "limit": 1},
                        )
                    else:
                        logger.info("📍 Нет pending updates")

            except Exception as e:
                logger.error(f"❌ Ошибка инициализации offset: {e}")

    async def send_response_to_user(self, chat_id: int, text: str):
        """Отправка ответа пользователю через Telegram API"""

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )

                if response.status_code == 200:
                    logger.info(f"✅ Ответ отправлен пользователю {chat_id}")
                    return True
                else:
                    logger.error(
                        f"❌ Ошибка отправки: {response.status_code} - {response.text}"
                    )
                    return False

            except Exception as e:
                logger.error(f"❌ Исключение при отправке: {e}")
                return False

    async def process_update(self, update: dict):
        """Обработка update через webhook с защитой от дублей"""

        # Защита от дублирующихся апдейтов
        update_id = update["update_id"]
        chat_id = update.get("message", {}).get("chat", {}).get("id")
        message_id = update.get("message", {}).get("message_id")

        if chat_id and message_id:
            update_fingerprint = f"dup:{chat_id}:{message_id}"
            try:
                # Проверяем, обрабатывали ли мы уже этот апдейт
                if not await self.redis_client.set(
                    update_fingerprint, 1, nx=True, ex=60
                ):
                    logger.info(f"🔄 Дубль апдейта {update_id} пропущен")
                    return True
            except Exception as e:
                logger.warning(f"Ошибка проверки дублей: {e}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                logger.info(f"🔄 Обрабатываем update {update['update_id']}")

                # Отправляем в webhook
                response = await client.post(
                    WEBHOOK_URL,
                    json=update,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Webhook обработал: {result.get('status')}")

                    # Если есть сообщение в update, получаем chat_id и отправляем ответ
                    if "message" in update:
                        chat_id = update["message"]["chat"]["id"]

                        # Здесь в реальной системе нужно получить сгенерированный ответ из FSM
                        # Для демонстрации отправляем простой ответ
                        await asyncio.sleep(1)  # Ждем обработки

                        # Webhook уже обработал сообщение и отправил ответ через FSM
                        # Дополнительный ответ от polling бота не нужен
                        logger.info(
                            "✅ Сообщение обработано webhook, ответ отправлен через FSM"
                        )

                    return True
                else:
                    logger.error(f"❌ Webhook ошибка: {response.status_code}")
                    return False

            except Exception as e:
                logger.error(f"❌ Ошибка обработки: {e}")
                return False

    async def get_updates(self):
        """Получение обновлений от Telegram"""

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={
                        "offset": self.last_update_id + 1,
                        "timeout": 5,
                        "allowed_updates": ["message"],
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    return result.get("result", [])
                else:
                    logger.error(f"❌ Ошибка getUpdates: {response.status_code}")
                    return []

            except Exception as e:
                logger.error(f"❌ Исключение getUpdates: {e}")
                return []

    async def run(self):
        """Основной цикл бота"""

        logger.info("🚀 Запускаем SoVAni AI Seller Polling Bot")
        logger.info(f"🔗 Webhook URL: {WEBHOOK_URL}")

        # Инициализируем offset
        await self.initialize_offset()

        while self.running:
            try:
                updates = await self.get_updates()

                for update in updates:
                    self.last_update_id = update["update_id"]

                    logger.info(f"📨 Новый update: {self.last_update_id}")

                    if "message" in update:
                        msg = update["message"]
                        user = msg.get("from", {})
                        text = msg.get("text", "Не текст")

                        logger.info(
                            f"👤 От: {user.get('first_name', 'Unknown')} (@{user.get('username', 'no_username')})"
                        )
                        logger.info(f"💬 Текст: {text}")

                        # Обрабатываем через webhook
                        await self.process_update(update)

                if not updates:
                    await asyncio.sleep(1)

            except KeyboardInterrupt:
                logger.info("👋 Остановка бота...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в основном цикле: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    # PROD GUARD: Polling разрешен только в dev режиме
    bot_mode = os.getenv("BOT_MODE", "webhook")
    if bot_mode != "polling":
        logger.warning(
            f"⚠️ PROD GUARD: BOT_MODE={bot_mode}, polling запрещен в production"
        )
        logger.info("💡 Для dev-режима установите: BOT_MODE=polling")
        sys.exit(0)

    logger.info("🔧 DEV MODE: Polling bot разрешен")
    bot = PollingBot()
    asyncio.run(bot.run())
