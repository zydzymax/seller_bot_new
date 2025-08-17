"""
Юнит-тесты для FlowManager (dialog/flow_manager.py)
Требуется: pytest, asyncio, unittest.mock
"""

import sys
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dialog.flow_manager import FlowManager, FlowTimeout, FlowValidationError, VersionConflictError

@pytest.mark.asyncio
async def test_flow_manager_successful_flow():
    fm = FlowManager(redis_url="redis://localhost")
    # Mock все зависимости
    fm.session_store = MagicMock()
    fm.session_store.get_with_version = AsyncMock(return_value=(MagicMock(), 1))
    fm.session_store.set_with_version = AsyncMock(return_value=None)
    fm.fsm = MagicMock()
    fm.fsm.handle = AsyncMock(return_value=("ok", MagicMock()))
    fm.openai_adapter = MagicMock()
    fm.openai_adapter.generate = AsyncMock(return_value="AI ответ")

    res = await fm.process(user_id=1, message="Тест", context=None)
    assert res == "AI ответ"

@pytest.mark.asyncio
async def test_flow_manager_openai_error():
    fm = FlowManager(redis_url="redis://localhost")
    fm.session_store = MagicMock()
    fm.session_store.get_with_version = AsyncMock(return_value=(MagicMock(), 1))
    fm.session_store.set_with_version = AsyncMock(return_value=None)
    fm.fsm = MagicMock()
    fm.fsm.handle = AsyncMock(return_value=("ok", MagicMock()))
    # Генерируем ошибку OpenAI
    class DummyOpenAIError(Exception): pass
    fm.openai_adapter = MagicMock()
    fm.openai_adapter.generate = AsyncMock(side_effect=DummyOpenAIError("fail"))

    res = await fm.process(user_id=1, message="Тест", context=None)
    assert "ошибка" in res.lower()  # Ожидаем fallback-ответ

@pytest.mark.asyncio
async def test_flow_manager_version_conflict_retry():
    fm = FlowManager(redis_url="redis://localhost")
    fm.session_store = MagicMock()
    # Сначала VersionConflict, потом успех
    fm.session_store.get_with_version = AsyncMock(side_effect=[
        VersionConflictError("conflict"),
        (MagicMock(), 1)
    ])
    fm.session_store.set_with_version = AsyncMock(return_value=None)
    fm.fsm = MagicMock()
    fm.fsm.handle = AsyncMock(return_value=("ok", MagicMock()))
    fm.openai_adapter = MagicMock()
    fm.openai_adapter.generate = AsyncMock(return_value="AI ответ")
    res = await fm.process(user_id=2, message="Тест", context=None)
    assert res == "AI ответ"

@pytest.mark.asyncio
async def test_flow_manager_validation_error():
    fm = FlowManager(redis_url="redis://localhost")
    fm.session_store = MagicMock()
    fm.session_store.get_with_version = AsyncMock(return_value=(MagicMock(), 1))
    fm.fsm = MagicMock()
    fm.fsm.handle = AsyncMock(side_effect=FlowValidationError("fail"))
    with pytest.raises(FlowValidationError):
        await fm.process(user_id=3, message="err", context=None)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__])

