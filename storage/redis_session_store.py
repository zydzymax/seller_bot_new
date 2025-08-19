"""
redis_session_store.py — временная in-memory реализация хранилища сессий для FSM SoVAni.
(В проде заменить на реальный Redis-клиент!)
"""

class RedisSessionStore:
    def __init__(self, redis_url: str, ttl: int = 1800):
        # Вместо реального Redis — временное хранилище в памяти (dict)
        self._storage = {}
        self.ttl = ttl

    async def get_with_version(self, session_id: str):
        # Возвращает (ctx, version)
        data = self._storage.get(session_id)
        if not data:
            return None, 0
        ctx, version = data
        return ctx, version

    async def set_with_version(self, session_id: str, ctx, version: int, reset_ttl: bool = False):
        # Оптимистичная блокировка: обновляем только если версия совпала
        existing = self._storage.get(session_id)
        current_version = existing[1] if existing else 0
        if current_version != version:
            raise Exception("VersionConflictError")
        # Save with incremented version
        self._storage[session_id] = (ctx, version + 1)

    async def delete(self, session_id: str):
        if session_id in self._storage:
            del self._storage[session_id]

