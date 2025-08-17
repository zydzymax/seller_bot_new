"""
Юнит-тесты для InMemoryFloodControl (antiflood.py).
Требуется: pytest, asyncio
"""

import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.antiflood import InMemoryFloodControl

@pytest.mark.asyncio
async def test_flood_limit_basic():
    flood = InMemoryFloodControl(rate_limit=3, interval_sec=2)
    user_id = 123
    # 3 запроса подряд — не флуд
    for _ in range(3):
        assert not await flood.is_flooding(user_id)
    # 4-й — уже флуд
    assert await flood.is_flooding(user_id)

@pytest.mark.asyncio
async def test_flood_limit_reset():
    flood = InMemoryFloodControl(rate_limit=2, interval_sec=1)
    user_id = 456
    assert not await flood.is_flooding(user_id)
    assert not await flood.is_flooding(user_id)
    assert await flood.is_flooding(user_id)
    # Ждём очистки окна
    await asyncio.sleep(1.1)
    assert not await flood.is_flooding(user_id)

@pytest.mark.asyncio
async def test_flood_multiuser():
    flood = InMemoryFloodControl(rate_limit=2, interval_sec=1)
    u1, u2 = 1001, 1002
    assert not await flood.is_flooding(u1)
    assert not await flood.is_flooding(u2)
    assert not await flood.is_flooding(u1)
    assert not await flood.is_flooding(u2)
    assert await flood.is_flooding(u1)
    assert await flood.is_flooding(u2)

@pytest.mark.asyncio
async def test_no_flood_on_sparse_requests():
    flood = InMemoryFloodControl(rate_limit=2, interval_sec=1)
    user_id = 999
    assert not await flood.is_flooding(user_id)
    await asyncio.sleep(1.1)
    assert not await flood.is_flooding(user_id)
    await asyncio.sleep(1.1)
    assert not await flood.is_flooding(user_id)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__])

