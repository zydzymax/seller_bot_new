"""
Тесты для исправления критического бага в состоянии QUANTITY_COLORS
"""

import pytest
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent.parent))

from dialog.flow_manager import (
    FlowManager, DialogContext, FlowState, 
    _wordnums_to_digits, _has_color_intent, _extract_first_int
)


def test_wordnums_to_digits():
    """Тест конвертации русских числительных в цифры"""
    assert _wordnums_to_digits("три цвета") == "3 цвета"
    assert _wordnums_to_digits("давайте четыре") == "давайте 4"
    assert _wordnums_to_digits("пять штук") == "5 штук"


def test_has_color_intent():
    """Тест определения намерения указать цвета"""
    assert _has_color_intent("давайте 3 цвета")
    assert _has_color_intent("сделаем четыре цвета")
    assert _has_color_intent("пусть будет 2 цвета")
    assert _has_color_intent("уменьшим до трех")
    assert _has_color_intent("три цвета")
    assert not _has_color_intent("3000 штук")
    assert not _has_color_intent("общий тираж")


def test_extract_first_int():
    """Тест извлечения первого числа"""
    assert _extract_first_int("давайте 3 цвета") == 3
    assert _extract_first_int("сделаем 5 вариантов") == 5
    assert _extract_first_int("нет чисел") is None
    assert _extract_first_int("123 456") == 123


class TestQuantityColorsOverride:
    """Тесты переопределения colors_count в состоянии QUANTITY_COLORS"""
    
    def setup_method(self):
        self.flow_manager = FlowManager()
        
    def test_override_colors_in_quantity_colors(self):
        """Тест переопределения colors_count: 4 → 3"""
        # Контекст: было 4 цвета, общий тираж 3000
        context = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=3000,
            colors_count=4,
            work_scheme="turnkey"
        )
        
        # Сообщение: "давайте тогда 3"
        message = "давайте тогда 3"
        extracted = self.flow_manager.extract_order_data(message, context)
        
        # Ожидаем: colors_count перезаписан на 3
        assert extracted["colors_count"] == 3
        
    def test_color_intent_overrides_existing_value(self):
        """Тест что цветовое намерение всегда переопределяет существующее значение"""
        context = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=3000,
            colors_count=4,  # Было 4
            work_scheme="turnkey"
        )
        
        # Различные способы сказать "3 цвета"
        test_cases = [
            "давайте 3 цвета",
            "сделаем 3",
            "пусть будет 3 цвета", 
            "уменьшим до 3",
            "изменим на 3 цвета"
        ]
        
        for message in test_cases:
            extracted = self.flow_manager.extract_order_data(message, context)
            assert extracted["colors_count"] == 3, f"Не сработало для: {message}"
    
    def test_fallback_logic_when_no_color_intent(self):
        """Тест фолбэк логики когда нет явного цветового намерения"""
        # Случай 1: нет colors_count, число интерпретируется как цвета
        context1 = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=3000,
            colors_count=None,
            work_scheme="turnkey"
        )
        
        extracted1 = self.flow_manager.extract_order_data("5", context1)
        assert extracted1["colors_count"] == 5
        
        # Случай 2: простой тест что числа без цветового намерения не переопределяют colors_count
        context2 = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=None,
            colors_count=3,  # уже установлено
            work_scheme="turnkey"
        )
        
        # Простое число без намерения - не должно переопределить colors_count
        extracted2 = self.flow_manager.extract_order_data("5000", context2)
        assert "colors_count" not in extracted2 or extracted2.get("colors_count") == 3
    
    def test_forced_response_uses_fresh_values(self):
        """Тест что принудительный ответ использует новые значения"""
        context = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=3000,
            colors_count=4,  # Было 4
            work_scheme="turnkey"
        )
        
        # После извлечения данных обновляем контекст (как в реальном коде)
        extracted = self.flow_manager.extract_order_data("давайте 3 цвета", context)
        context.colors_count = extracted["colors_count"]  # 3
        
        # Принудительный ответ должен использовать 3, а не 4
        response = self.flow_manager._get_forced_response(context, "давайте 3 цвета", {})
        
        assert "3 цветах" in response or "3 цвета" in response
        assert "4 цвет" not in response  # Не должно упоминать старое значение


class TestTransitionToMOQValidation:
    """Тест переходов в MOQ_VALIDATION после обновления цветов"""
    
    def setup_method(self):
        self.flow_manager = FlowManager()
    
    def test_transition_after_successful_override(self):
        """Тест перехода в следующее состояние после успешного переопределения"""
        context = DialogContext(
            user_id=123,
            chat_id=456,
            current_state=FlowState.QUANTITY_COLORS,
            total_quantity=3000,
            colors_count=4,
            work_scheme="turnkey"
        )
        
        # Обновляем как в реальном коде
        extracted = self.flow_manager.extract_order_data("давайте 3 цвета", context)
        context.colors_count = extracted["colors_count"]  # 3
        
        # Теперь у нас есть оба параметра: total_quantity=3000, colors_count=3
        # Количество на цвет: 3000/3 = 1000 >= 1000 (минимум для turnkey)
        response = self.flow_manager._get_forced_response(context, "давайте 3 цвета", {})
        
        # Ответ должен быть успешным (минимумы соблюдены)
        assert "минимумы соблюдены" in response.lower()
        assert "1000 шт на цвет" in response


def test_no_repeat_work_scheme_if_set():
    """Тест что не повторяем вопрос о схеме работы если она уже установлена"""
    flow_manager = FlowManager()
    
    context = DialogContext(
        user_id=123,
        chat_id=456,
        current_state=FlowState.WORK_SCHEME,
        work_scheme="turnkey"  # Уже установлена
    )
    
    response = flow_manager._get_forced_response(context, "под ключ", {})
    
    # Не должно содержать вопрос о выборе схемы
    assert "как будем работать" not in response.lower()
    # Должно содержать подтверждение и следующий вопрос
    assert "сколько цветов" in response.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])