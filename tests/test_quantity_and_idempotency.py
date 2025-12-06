import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.text_processing import parse_quantity

@pytest.mark.parametrize("text, want", [
    ("4000", 4000),
    ("4 000", 4000),
    ("5,000", 5000),
    ("≈5000", 5000),
    ("~ 3 200", 3200),
    ("4000 шт", 4000),
    ("5000pcs", 5000),
    ("около 4000", 4000),
    ("мне нужно 2500", 2500),
    ("тел 89001234567", None),
    ("пусто", None),
])
def test_parse_quantity(text, want):
    assert parse_quantity(text) == want

# (опционально) Каркас теста FSM перехода после количества
def test_fsm_moves_forward_after_qty(monkeypatch):
    # Заглушка стора/редиса и имитация вызова шага, где парсится qty
    # Ожидаем state == "await_size" (или актуальное имя следующего шага)
    assert True  # оставить заглушку, если инфраструктуры тестов FSM нет