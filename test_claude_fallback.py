#!/usr/bin/env python3
"""
Тест Claude API fallback для SoVAni AI-продавца.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from llm.orchestrator import get_orchestrator
from utils.logging import get_logger

logger = get_logger(__name__)


async def test_claude_fallback():
    """Тест работы Claude fallback при недоступности OpenAI"""
    
    print("🚀 Тестирование Claude API fallback...")
    
    # Получаем оркестратор
    orchestrator = await get_orchestrator()
    
    # Проверяем health check
    print("\n📊 Health check LLM оркестратора:")
    health = await orchestrator.health_check()
    print(json.dumps(health, indent=2, ensure_ascii=False))
    
    # Тестируем генерацию ответа
    print("\n💬 Тестирование генерации ответа...")
    
    try:
        response = await orchestrator.generate_response(
            user_prompt="Привет! Расскажи о фабрике SoVAni и её продукции текстиля",
            context={
                "user_id": "test_user",
                "chat_id": "test_chat", 
                "session_id": "test_session"
            }
        )
        
        print(f"\n✅ Ответ получен:")
        print(f"Модель: {response.model_used}")
        print(f"Токены: {response.tokens_used}")
        print(f"Стоимость: {response.cost_rub:.2f} руб.")
        print(f"Кэшировано: {response.cached}")
        print(f"Время ответа: {response.response_time_ms}ms")
        print(f"Контент: {response.content[:200]}...")
        
    except Exception as e:
        print(f"❌ Ошибка при генерации ответа: {e}")
        
    # Закрываем соединения
    await orchestrator.close()
    

async def test_invalid_openai_key():
    """Тест с заведомо неверным OpenAI ключом для проверки fallback"""
    
    print("\n🔄 Тестирование fallback с неверным OpenAI ключом...")
    
    # Временно заменяем ключ на неверный
    original_key = os.getenv('OPENAI_API_KEY')
    os.environ['OPENAI_API_KEY'] = 'invalid_key_test'
    
    try:
        orchestrator = await get_orchestrator()
        
        response = await orchestrator.generate_response(
            user_prompt="Привет! Это тест fallback на Claude API",
            context={"test": True}
        )
        
        print(f"\n✅ Fallback работает:")
        print(f"Модель: {response.model_used}")
        print(f"Контент: {response.content[:200]}...")
        
        # Восстанавливаем оригинальный ключ
        if original_key:
            os.environ['OPENAI_API_KEY'] = original_key
        
        await orchestrator.close()
        
    except Exception as e:
        print(f"❌ Ошибка fallback: {e}")
        
        # Восстанавливаем ключ в случае ошибки
        if original_key:
            os.environ['OPENAI_API_KEY'] = original_key


if __name__ == "__main__":
    print("🤖 SoVAni AI Seller - Claude Fallback Test")
    print("=" * 50)
    
    asyncio.run(test_claude_fallback())
    asyncio.run(test_invalid_openai_key())
    
    print("\n✨ Тестирование завершено!")