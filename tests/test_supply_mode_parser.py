"""
Тесты для supply_mode_parser.py - парсер режима поставки

ГОРЯЧИЙ ПАТЧ: Тесты надёжности парсинга "под ключ"/"давальческое сырьё"
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.supply_mode_parser import parse_supply_mode


class TestSupplyModeParser:

    def test_pod_kluch_variants(self):
        """Тесты для распознавания 'под ключ'"""
        # Основные варианты
        assert parse_supply_mode("под ключ") == "pod_kluch"
        assert parse_supply_mode("подключ") == "pod_kluch"
        assert parse_supply_mode("под-ключ") == "pod_kluch"

        # С пробелами и регистром
        assert parse_supply_mode("ПОД КЛЮЧ") == "pod_kluch"
        assert parse_supply_mode("  под ключ  ") == "pod_kluch"
        assert parse_supply_mode("Под ключ") == "pod_kluch"

        # В контексте
        assert parse_supply_mode("хочу под ключ") == "pod_kluch"
        assert parse_supply_mode("давайте под ключ работать") == "pod_kluch"
        assert parse_supply_mode("можно полный цикл?") == "pod_kluch"
        assert parse_supply_mode("всё под ключ делаете?") == "pod_kluch"

        # Синонимы
        assert parse_supply_mode("полный цикл") == "pod_kluch"
        assert parse_supply_mode("из наших материалов") == "pod_kluch"
        assert parse_supply_mode("мы закупим всё") == "pod_kluch"
        assert parse_supply_mode("без ваших материалов") == "pod_kluch"

    def test_davalskoe_variants(self):
        """Тесты для распознавания 'давальческое сырьё'"""
        # Основные варианты
        assert parse_supply_mode("давальческое сырьё") == "davalskoe"
        assert parse_supply_mode("давальческое сырье") == "davalskoe"
        assert parse_supply_mode("давальческое") == "davalskoe"

        # С пробелами и регистром
        assert parse_supply_mode("ДАВАЛЬЧЕСКОЕ СЫРЬЁ") == "davalskoe"
        assert parse_supply_mode("  давальческое сырьё  ") == "davalskoe"
        assert parse_supply_mode("Давальческое сырье") == "davalskoe"

        # В контексте
        assert parse_supply_mode("у нас давальческое сырьё") == "davalskoe"
        assert parse_supply_mode("работаем из моих материалов") == "davalskoe"
        assert parse_supply_mode("ваше сырьё использовать") == "davalskoe"
        assert parse_supply_mode("материал клиента") == "davalskoe"

        # Падежи
        assert parse_supply_mode("давальческий режим") == "davalskoe"
        assert parse_supply_mode("давальческого сырья") == "davalskoe"

    def test_no_match(self):
        """Тесты для случаев, когда режим не распознан"""
        assert parse_supply_mode("") is None
        assert parse_supply_mode(None) is None
        assert parse_supply_mode("просто текст") is None
        assert parse_supply_mode("хочу заказать") is None
        assert parse_supply_mode("футболки") is None
        assert parse_supply_mode("123") is None
        assert parse_supply_mode("ключи от дома") is None  # ложное срабатывание
        assert parse_supply_mode("сырой картофель") is None  # ложное срабатывание

    def test_priority_cases(self):
        """Тесты приоритетов при наличии нескольких ключевых слов"""
        # Если в тексте есть и "под ключ", и "давальческое" - берём первое найденное
        mixed_text = "можно под ключ, а не давальческое сырьё"
        assert parse_supply_mode(mixed_text) == "pod_kluch"  # первое в regex поиске

    def test_real_user_messages(self):
        """Тесты на реальных пользовательских сообщениях"""
        # Примеры из реальных диалогов
        assert parse_supply_mode("хотим под ключ работать с вами") == "pod_kluch"
        assert parse_supply_mode("у меня есть ткань, давальческое") == "davalskoe"
        assert parse_supply_mode("полностью под ключ нужно") == "pod_kluch"
        assert parse_supply_mode("из своих материалов можно?") == "davalskoe"
        assert parse_supply_mode("вы ткань закупаете сами?") == "pod_kluch"

        # Негативные случаи
        assert parse_supply_mode("сколько это стоит?") is None
        assert parse_supply_mode("какие у вас ткани?") is None
        assert parse_supply_mode("когда будет готово?") is None


if __name__ == "__main__":
    # Простой запуск без pytest
    test = TestSupplyModeParser()

    print("🧪 Тестирование supply_mode_parser...")

    # Запуск всех тестов
    test.test_pod_kluch_variants()
    print("✅ pod_kluch варианты")

    test.test_davalskoe_variants()
    print("✅ davalskoe варианты")

    test.test_no_match()
    print("✅ отсутствие совпадений")

    test.test_priority_cases()
    print("✅ приоритеты")

    test.test_real_user_messages()
    print("✅ реальные сообщения")

    print("🎉 Все тесты прошли успешно!")
