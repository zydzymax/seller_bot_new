#!/usr/bin/env python3
"""
Тест Telegram webhook для SoVAni AI-продавца.
"""

import asyncio
import json
import httpx
import time

# Создаем тестовый Telegram update
test_update = {
    "update_id": int(time.time()),
    "message": {
        "message_id": 123,
        "date": int(time.time()),
        "text": "Привет! Расскажи о фабрике SoVAni",
        "from": {
            "id": 12345,
            "first_name": "Тест",
            "username": "test_user",
            "language_code": "ru"
        },
        "chat": {
            "id": 12345,
            "type": "private"
        }
    }
}

async def test_webhook():
    """Тест webhook эндпоинта"""
    
    print("🚀 Тестирование Telegram webhook...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Отправляем тестовый update
            response = await client.post(
                "http://localhost:8000/telegram/SECRET",
                json=test_update,
                headers={"Content-Type": "application/json"}
            )
            
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print("✅ Webhook успешно обработал запрос:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(f"❌ Ошибка webhook: {response.status_code}")
                print(response.text)
                
        except Exception as e:
            print(f"❌ Ошибка подключения: {e}")

if __name__ == "__main__":
    print("🤖 SoVAni AI Seller - Webhook Test")
    print("=" * 40)
    
    asyncio.run(test_webhook())