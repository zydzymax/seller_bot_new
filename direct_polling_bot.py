#!/usr/bin/env python3
"""
Direct Telegram polling bot - использует flow_manager напрямую (горячий патч)
"""

import asyncio
import json
import time
import os
import sys
from utils.env_resolver import is_prod

# Guard: запрет polling в production
if is_prod():
    print("ERROR: polling_forbidden_in_prod")
    sys.exit(1)
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Bot
from telegram.error import TelegramError
import redis.asyncio as redis

from utils.logging import get_logger
from dialog.flow_manager import get_flow_manager

logger = get_logger(__name__)

TELEGRAM_TOKEN = ""

class DirectPollingBot:
    """Direct polling бот с обновленным flow_manager"""
    
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_TOKEN)
        self.last_update_id = 0
        self.flow_manager = None
        self.redis_client = None
        self.processed_updates = set()
        
    async def initialize(self):
        """Инициализация компонентов"""
        try:
            # Устанавливаем переменные окружения для flow_manager
            os.environ['TELEGRAM_TOKEN'] = TELEGRAM_TOKEN
            os.environ['REDIS_ADDR'] = "redis://localhost:6379/0"
            
            # Инициализация Redis
            self.redis_client = redis.from_url("redis://localhost:6379/0")
            await self.redis_client.ping()
            logger.info("Redis подключен")
            
            # Инициализация Flow Manager
            self.flow_manager = await get_flow_manager("redis://localhost:6379/0")
            logger.info("Flow Manager инициализирован")
            
            logger.info("🚀 Direct Polling Bot инициализирован")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка инициализации: {e}")
            return False
    
    async def get_updates(self):
        """Получение обновлений от Telegram"""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {
                "limit": 100,
                "timeout": 5,
                "offset": self.last_update_id + 1 if self.last_update_id else None,
                "allowed_updates": ["message", "callback_query"]
            }
            
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params={k: v for k, v in params.items() if v is not None})
                
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return data.get("result", [])
                else:
                    logger.error(f"Telegram API error: {data}")
            else:
                logger.error(f"HTTP error {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Ошибка получения updates: {e}")
            
        return []
    
    async def process_update(self, update):
        """Обработка обновления через flow_manager"""
        try:
            update_id = update.get("update_id")
            
            # Защита от дубликатов
            if update_id in self.processed_updates:
                return
            self.processed_updates.add(update_id)
            
            # Ограничиваем размер кэша
            if len(self.processed_updates) > 1000:
                self.processed_updates.clear()
            
            logger.info(f"📨 Обрабатываем update {update_id}")
            
            # Обработка сообщения
            if "message" in update:
                await self.process_message(update)
            elif "callback_query" in update:
                await self.process_callback_query(update)
            else:
                logger.warning(f"Неизвестный тип update: {update.keys()}")
                
        except Exception as e:
            logger.error(f"Ошибка обработки update {update.get('update_id')}: {e}")
    
    async def process_message(self, update):
        """Обработка текстового сообщения"""
        try:
            message = update["message"]
            
            user_info = {
                "chat_id": message["chat"]["id"],
                "user_id": message["from"]["id"],
                "username": message["from"].get("username", ""),
                "first_name": message["from"].get("first_name", "")
            }
            
            message_text = message.get("text", "")
            
            message_data = {
                "message_id": message["message_id"],
                "date": message["date"]
            }
            
            logger.info(f"👤 От: {user_info['first_name']} (@{user_info['username']})")
            logger.info(f"💬 Текст: {message_text}")
            
            # Обработка через flow_manager
            result = await self.flow_manager.process_message(
                user_info, message_text, message_data, update
            )
            
            logger.info(f"✅ Сообщение обработано, состояние: {result.get('state')}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
    
    async def process_callback_query(self, update):
        """Обработка callback query"""
        try:
            callback_query = update["callback_query"]
            
            user_info = {
                "chat_id": callback_query["from"]["id"],
                "user_id": callback_query["from"]["id"],
                "username": callback_query["from"].get("username", ""),
                "first_name": callback_query["from"].get("first_name", "")
            }
            
            callback_data = callback_query.get("data", "")
            
            logger.info(f"📞 Callback query: {callback_data}")
            
            # Обработка через flow_manager
            result = await self.flow_manager.process_callback_query(
                user_info, callback_data, update
            )
            
            logger.info(f"✅ Callback обработан: {result.get('callback_processed')}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки callback query: {e}")
    
    async def run(self):
        """Основной цикл polling"""
        logger.info("🔄 Запуск polling...")
        
        while True:
            try:
                updates = await self.get_updates()
                
                if not updates:
                    await asyncio.sleep(1)
                    continue
                
                logger.info(f"📥 Получено {len(updates)} updates")
                
                # Обработка всех updates
                for update in updates:
                    await self.process_update(update)
                    self.last_update_id = update.get("update_id", self.last_update_id)
                
            except Exception as e:
                logger.error(f"Ошибка в polling цикле: {e}")
                await asyncio.sleep(5)

async def main():
    """Точка входа"""
    # PROD GUARD: Direct Polling запрещен в production
    bot_mode = os.getenv("BOT_MODE", "webhook")
    if bot_mode != "polling":
        logger.warning(f"⚠️ PROD GUARD: BOT_MODE={bot_mode}, direct polling запрещен в production")
        logger.info("💡 Для dev-режима установите: BOT_MODE=polling")
        return 0
    
    logger.info("🔧 DEV MODE: Direct Polling bot разрешен")
    bot = DirectPollingBot()
    
    if await bot.initialize():
        logger.info("🎯 ГОРЯЧИЙ ПАТЧ АКТИВЕН - Direct Polling Bot запущен")
        await bot.run()
    else:
        logger.error("❌ Не удалось инициализировать бота")
        return 1

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)