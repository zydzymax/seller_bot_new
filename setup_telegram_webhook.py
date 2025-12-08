#!/usr/bin/env python3
"""
Настройка Telegram webhook для SoVAni AI-продавца.
"""

import asyncio
import httpx
import os

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
WEBHOOK_URL = "https://your-domain.com/telegram/SECRET"  # Замените на ваш домен

async def setup_webhook():
    """Настройка webhook для Telegram бота"""
    
    print("🔧 Настройка Telegram webhook...")
    
    async with httpx.AsyncClient() as client:
        try:
            # Получаем информацию о боте
            response = await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe")
            if response.status_code == 200:
                bot_info = response.json()
                print(f"✅ Бот найден: @{bot_info['result']['username']}")
                print(f"   ID: {bot_info['result']['id']}")
                print(f"   Имя: {bot_info['result']['first_name']}")
            else:
                print(f"❌ Ошибка получения информации о боте: {response.status_code}")
                return
                
            # Устанавливаем webhook (для демонстрации - удаляем его)
            print("\n🗑️  Удаляем webhook для тестирования...")
            delete_response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
            )
            
            if delete_response.status_code == 200:
                print("✅ Webhook удален")
            
            # Получаем информацию о webhook
            webhook_info = await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo")
            if webhook_info.status_code == 200:
                info = webhook_info.json()['result']
                print(f"\n📋 Информация о webhook:")
                print(f"   URL: {info.get('url', 'Не установлен')}")
                print(f"   Pending updates: {info.get('pending_update_count', 0)}")
                print(f"   Last error: {info.get('last_error_message', 'Нет')}")
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")

async def test_bot_locally():
    """Тест бота в режиме polling для демонстрации"""
    
    print("\n🤖 Тестирование бота в режиме polling...")
    
    async with httpx.AsyncClient() as client:
        offset = 0
        
        while True:
            try:
                # Получаем обновления
                response = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 2}
                )
                
                if response.status_code == 200:
                    updates = response.json()['result']
                    
                    for update in updates:
                        offset = update['update_id'] + 1
                        
                        print(f"\n📨 Получено сообщение:")
                        print(f"   Update ID: {update['update_id']}")
                        
                        if 'message' in update:
                            msg = update['message']
                            print(f"   От: {msg.get('from', {}).get('first_name', 'Unknown')}")
                            print(f"   Текст: {msg.get('text', 'Не текст')}")
                            
                            # Отправляем update в наш webhook
                            webhook_response = await client.post(
                                "http://localhost:8000/telegram/SECRET",
                                json=update,
                                timeout=30.0
                            )
                            
                            print(f"   Webhook ответ: {webhook_response.status_code}")
                            if webhook_response.status_code == 200:
                                result = webhook_response.json()
                                print(f"   Статус: {result.get('status')}")
                
            except KeyboardInterrupt:
                print("\n👋 Остановка тестирования...")
                break
            except Exception as e:
                print(f"❌ Ошибка polling: {e}")
                await asyncio.sleep(1)

if __name__ == "__main__":
    print("🚀 SoVAni AI Seller - Telegram Setup")
    print("=" * 45)
    
    print("1. Настройка webhook...")
    asyncio.run(setup_webhook())
    
    print("\n" + "=" * 45)
    print("2. Для тестирования отправьте сообщение боту и нажмите Enter")
    print("   (Ctrl+C для остановки)")
    
    try:
        input("Нажмите Enter для начала тестирования...")
        asyncio.run(test_bot_locally())
    except KeyboardInterrupt:
        print("\n✅ Тестирование завершено")