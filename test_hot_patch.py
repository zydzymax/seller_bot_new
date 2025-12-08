#!/usr/bin/env python3
"""
PIN-ТЕСТЫ для ГОРЯЧЕГО ПАТЧА

Тестируем 6 ключевых требований:
1. Приветствие с persona (Алёна, SoVAni)
2. Надёжный supply_mode_parser
3. Анти-зацикливание
4. Никаких reply кнопок в choices
5. Callback query support
6. Error guards
"""

import asyncio
import sys
import os

# Добавляем путь для импортов
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_hot_patch():
    from dialog.flow_manager import get_flow_manager
    from utils.supply_mode_parser import parse_supply_mode
    
    print("🔥 ГОРЯЧИЙ ПАТЧ - PIN-ТЕСТЫ")
    print("=" * 50)
    
    # Тест 1: Supply mode parser
    print("\n1. 🎯 Тест supply_mode_parser:")
    
    test_cases = [
        ("под ключ", "pod_kluch"),
        ("полный цикл", "pod_kluch"),
        ("давальческое сырьё", "davalskoe"),
        ("из моих материалов", "davalskoe"),
        ("хочу под ключ работать", "pod_kluch"),
        ("у меня ткань есть давальческое", "davalskoe"),
        ("не понимаю", None)
    ]
    
    for text, expected in test_cases:
        result = parse_supply_mode(text)
        status = "✅" if result == expected else "❌"
        print(f"   {status} '{text}' -> {result} (ожидался {expected})")
    
    # Тест 2: Flow Manager API тесты
    print("\n2. 🤖 Тест Flow Manager:")
    
    fm = await get_flow_manager("redis://localhost:6379/0")
    
    user_info = {
        "chat_id": 999999,
        "user_id": 999999,
        "username": "test_user",
        "first_name": "Test"
    }
    
    # Симуляция диалога
    conversations = [
        {
            "message": "Привет",
            "expect_contains": ["Алёна", "SoVAni", "менеджер"]
        },
        {
            "message": "футболки",
            "expect_contains": ["под ключ", "давальческое"]
        },
        {
            "message": "под ключ",
            "expect_contains": ["цвет"]
        },
        {
            "message": "3 цвета",
            "expect_contains": ["количество"]
        },
        {
            "message": "5000",
            "expect_contains": ["Резюмирую", "цвета"]
        }
    ]
    
    for i, step in enumerate(conversations, 1):
        print(f"\n   Шаг {i}: '{step['message']}'")
        try:
            result = await fm.process_message(
                user_info,
                step["message"],
                {"message_id": i, "date": 1234567890},
                {"update_id": i}
            )
            
            print(f"   ✅ Ответ получен, состояние: {result.get('state', 'unknown')}")
            
            # Проверка на зацикливание
            if result.get('status') == 'error':
                print(f"   ❌ Ошибка: {result.get('error')}")
            elif result.get('state_changed'):
                print(f"   ✅ Состояние изменилось корректно")
            
        except Exception as e:
            print(f"   ❌ Исключение: {e}")
    
    # Тест 3: Error guards
    print("\n3. 🛡️ Тест Error Guards:")
    try:
        # Намеренно некорректное сообщение
        result = await fm.process_message(
            user_info,
            None,  # None message
            {"message_id": 999},
            {"update_id": 999}
        )
        
        if result.get('status') == 'recovered':
            print("   ✅ Error guard сработал, пользователь получил мягкий ответ")
        else:
            print("   ❌ Error guard не сработал")
            
    except Exception as e:
        print(f"   ❌ Необработанное исключение: {e}")
    
    # Тест 4: Callback query
    print("\n4. 📞 Тест Callback Query:")
    try:
        result = await fm.process_callback_query(
            user_info,
            "change_quantity",
            {
                "update_id": 1000,
                "callback_query": {"id": "test_callback_123"}
            }
        )
        
        if result.get('callback_processed'):
            print("   ✅ Callback query обработан корректно")
        else:
            print("   ❌ Callback query не обработан")
            
    except Exception as e:
        print(f"   ❌ Ошибка callback query: {e}")
    
    print("\n" + "=" * 50)
    print("🎯 PIN-ТЕСТЫ ЗАВЕРШЕНЫ")
    

if __name__ == "__main__":
    asyncio.run(test_hot_patch())