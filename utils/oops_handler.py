import time
import redis.asyncio as redis
import os

_redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


async def should_oops(chat_id: int, step: str) -> bool:
    """Проверяет, нужна ли переформулировка (если недавно задавали этот же step)"""
    key = f"oops:{chat_id}:{step}"
    try:
        last_time = await _redis_client.get(key)
        if last_time:
            last_timestamp = float(last_time)
            if time.time() - last_timestamp <= 120:  # 120 секунд
                return True

        # Записываем текущее время
        await _redis_client.setex(key, 300, str(time.time()))  # TTL 5 минут
        return False
    except Exception:
        return False


def build_oops(step: str) -> dict:
    """Строит переформулировку с вариантами"""
    oops_variants = {
        "ask.total_qty": {
            "text": "Уточните количество изделий:\n• 3000 • 4000 • 5000 • Другое",
            "keyboard": [["3000"], ["4000"], ["5000"], ["Другое"]],
        },
        "ask.colors_count": {
            "text": "Выберите количество цветов:\n• 1 • 2 • 3 • 4 • 5 • Другое",
            "keyboard": [["1"], ["2"], ["3"], ["4"], ["5"], ["Другое"]],
        },
        "ask.supply_mode": {
            "text": "Как работаем с материалами:\n• Под ключ • Давальческое сырьё • Другое",
            "keyboard": [["Под ключ"], ["Давальческое сырьё"], ["Другое"]],
        },
        "ask.product_type": {
            "text": "Какое изделие планируете:\n• Футболка • Худи • Другое",
            "keyboard": [["Футболка"], ["Худи"], ["Другое"]],
        },
    }

    return oops_variants.get(
        step, {"text": "Не понял. Можете уточнить?", "keyboard": None}
    )
