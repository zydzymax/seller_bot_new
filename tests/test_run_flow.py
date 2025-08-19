import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import asyncio
from dialog.flow_manager import FlowManager

async def main():
    # Выбери нужного LLM: "claude" или "openai"
    flow = FlowManager(redis_url="redis://localhost", llm_provider="claude")
    user_id = 123
    message = "Посоветуй мягкую пижаму для сна"
    # Допусти, что session_store уже замокан, либо замени на свою инициализацию.
    # Здесь пример только для демонстрации API (по факту session_store нужно или замокать, или реализовать!)
    try:
        response = await flow.process(user_id, message)
        print(f"Ответ AI:\n{response}")
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())

