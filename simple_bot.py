#!/usr/bin/env python3
"""
Простой тестовый бот для проверки системы слотов
"""

import asyncio
import httpx
import json
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from dialog.flow_manager import get_flow_manager
from utils.logging import get_logger

logger = get_logger(__name__)

TOKEN = ""

async def send_message(chat_id: int, text: str):
    """Отправка сообщения через Telegram API"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

async def get_updates(offset: int = 0):
    """Получение updates от Telegram"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 1, "limit": 10}
            )
            if response.status_code == 200:
                return response.json().get('result', [])
            else:
                logger.error(f"Ошибка getUpdates: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Исключение getUpdates: {e}")
            return []

async def main():
    """Основной цикл бота"""
    logger.info("🚀 Запуск простого тестового бота")
    
    # Получаем flow manager
    flow_manager = await get_flow_manager()
    
    # Устанавливаем высокий offset для пропуска старых updates
    last_update_id = 999999999
    logger.info(f"📍 Начинаем с offset: {last_update_id}")
    
    # Основной цикл
    while True:
        try:
            updates = await get_updates(last_update_id)
            
            for update in updates:
                update_id = update['update_id']
                last_update_id = update_id + 1
                
                if 'message' not in update:
                    continue
                    
                message = update['message']
                chat_id = message['chat']['id']
                user_id = message['from']['id']
                text = message.get('text', '')
                
                logger.info(f"📨 Сообщение от {user_id}: {text}")
                
                # Обрабатываем через flow manager
                user_info = {
                    'chat_id': chat_id,
                    'user_id': user_id,
                    'username': message['from'].get('username', ''),
                    'first_name': message['from'].get('first_name', '')
                }
                
                result = await flow_manager.process_message(
                    user_info=user_info,
                    message_text=text,
                    message_data=message,
                    update=update
                )
                
                logger.info(f"✅ Обработано: {result}")
            
            if not updates:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("👋 Остановка бота")
            break
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())