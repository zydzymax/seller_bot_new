import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.message_shaper import shape


def test_shape_ask_removes_emoji():
    """Проверяет, что из ask удаляются эмодзи"""
    text = "Какое количество нужно? 😊 👍"
    result = shape(text, "ask")
    assert "😊" not in result
    assert "👍" not in result


def test_shape_confirm_adds_emoji():
    """Проверяет, что в confirm добавляется эмодзи"""
    text = "Записал 4000 штук"
    result = shape(text, "confirm")
    assert "✅" in result


def test_shape_max_length():
    """Проверяет обрезку по max_chars"""
    long_text = "Очень длинный текст " * 50  # 1000+ символов
    result = shape(long_text, "ask")
    assert len(result) <= 320


def test_shape_removes_repeats():
    """Проверяет схлопывание повторов"""
    text = "Повтор повтор повтор вопрос?"
    result = shape(text, "ask")
    # Проверяем что в результате есть "Повтор" (с большой буквы) один раз
    assert result.count("Повтор") == 1
    assert result.count("повтор") == 0  # строчные удалились


def test_shape_removes_fillers():
    """Проверяет удаление филлеров"""
    text = "Итак, смотрите, какое количество нужно?"
    result = shape(text, "ask")
    assert "итак" not in result.lower()
    assert "смотрите" not in result.lower()
