"""
Тесты LLM-оркестратора для AI-продавца

Тест 1: Стройматериалы + возражение «дорого»
Тест 2: Онлайн-школа + возражение «дороговато»
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.llm_orchestrator import (
    select_model,
    BASE_MODEL,
    SENIOR_MODEL,
    LEADS_THRESHOLD,
)
from dialog.intent_helpers import is_price_objection


def test_price_objection_detection():
    """Тест детектора возражений по цене"""
    print("\n=== Тест детектора возражений по цене ===\n")

    # Должны определяться как возражения
    objections = [
        "мне кажется это дорого",
        "дороговато",
        "слишком дорого для нас",
        "нет такого бюджета",
        "не потянем",
        "не потяну такую сумму",
        "за такие деньги можно...",
        "это накладно",
        "цена кусается",
    ]

    # Не должны определяться как возражения
    non_objections = [
        "расскажите подробнее",
        "интересно",
        "а какие есть тарифы?",
        "давайте созвонимся",
        "хорошо, продолжим",
    ]

    print("Проверяем возражения по цене:")
    for text in objections:
        result = is_price_objection(text)
        status = "✓" if result else "✗"
        print(f"  {status} '{text}' -> {result}")
        assert result, f"Должно быть возражением: {text}"

    print("\nПроверяем НЕ-возражения:")
    for text in non_objections:
        result = is_price_objection(text)
        status = "✓" if not result else "✗"
        print(f"  {status} '{text}' -> {result}")
        assert not result, f"НЕ должно быть возражением: {text}"

    print("\n✓ Детектор возражений работает корректно")


def test_model_selection_logic():
    """Тест логики выбора модели"""
    print("\n=== Тест логики выбора модели ===\n")
    print(f"BASE_MODEL: {BASE_MODEL}")
    print(f"SENIOR_MODEL: {SENIOR_MODEL}")
    print(f"LEADS_THRESHOLD: {LEADS_THRESHOLD}")

    # Тест 1: main_pitch всегда использует BASE_MODEL
    print("\n--- Тест: main_pitch ---")
    context1 = {"leads_per_month": 3000, "decision_maker": "self"}
    model1 = select_model("main_pitch", context1)
    print(f"  mode=main_pitch, leads=3000, dm=self -> {model1}")
    assert model1 == BASE_MODEL, "main_pitch должен использовать BASE_MODEL"

    # Тест 2: price_objection с >= 100 лидов и ЛПР=self -> SENIOR_MODEL
    print("\n--- Тест: price_objection + серьезный клиент ---")
    context2 = {"leads_per_month": 3000, "decision_maker": "self"}
    model2 = select_model("price_objection", context2)
    print(f"  mode=price_objection, leads=3000, dm=self -> {model2}")
    assert (
        model2 == SENIOR_MODEL
    ), "price_objection с серьезным клиентом должен использовать SENIOR_MODEL"

    # Тест 3: price_objection с < 100 лидов -> BASE_MODEL
    print("\n--- Тест: price_objection + мало лидов ---")
    context3 = {"leads_per_month": 50, "decision_maker": "self"}
    model3 = select_model("price_objection", context3)
    print(f"  mode=price_objection, leads=50, dm=self -> {model3}")
    assert (
        model3 == BASE_MODEL
    ), "price_objection с малым количеством лидов должен использовать BASE_MODEL"

    # Тест 4: price_objection с не-ЛПР -> BASE_MODEL
    print("\n--- Тест: price_objection + не ЛПР ---")
    context4 = {"leads_per_month": 3000, "decision_maker": "partner"}
    model4 = select_model("price_objection", context4)
    print(f"  mode=price_objection, leads=3000, dm=partner -> {model4}")
    assert (
        model4 == BASE_MODEL
    ), "price_objection с партнером должен использовать BASE_MODEL"

    # Тест 5: generic всегда использует BASE_MODEL
    print("\n--- Тест: generic ---")
    context5 = {"leads_per_month": 3000, "decision_maker": "self"}
    model5 = select_model("generic", context5)
    print(f"  mode=generic, leads=3000, dm=self -> {model5}")
    assert model5 == BASE_MODEL, "generic должен использовать BASE_MODEL"

    print("\n✓ Логика выбора модели работает корректно")


def test_scenario_1_building_materials():
    """
    Тест 1: Продажа стройматериалов
    - 3000 заявок/мес
    - decision_maker: "я"
    - возражение: "мне кажется это дорого"

    Ожидание: SENIOR_MODEL для price_objection
    """
    print("\n=== Тест 1: Стройматериалы + 'дорого' ===\n")

    context = {
        "name": "Алексей",
        "niche": "Продажа стройматериалов",
        "leads_per_month": 3000,
        "channels": "сайт",
        "current_process": "менеджеры вручную",
        "pain": "потерянные заявки",
        "decision_maker": "self",  # "я" преобразуется в "self"
    }

    print("Контекст клиента:")
    for k, v in context.items():
        print(f"  {k}: {v}")

    # Шаг 1: main_pitch (первый ответ)
    print("\n1. Первый ответ (main_pitch):")
    mode1 = "main_pitch"
    model1 = select_model(mode1, context)
    print(f"   mode={mode1} -> model={model1}")
    assert model1 == BASE_MODEL

    # Шаг 2: Клиент говорит "дорого"
    user_message = "мне кажется это дорого"
    print(f"\n2. Клиент: '{user_message}'")

    is_objection = is_price_objection(user_message)
    print(f"   is_price_objection={is_objection}")
    assert is_objection

    mode2 = "price_objection"
    model2 = select_model(mode2, context)
    print(f"   mode={mode2} -> model={model2}")
    assert model2 == SENIOR_MODEL, "Должен использоваться SENIOR_MODEL!"

    print("\n✓ Тест 1 пройден: SENIOR_MODEL активируется для price_objection")
    print(f"  leads={context['leads_per_month']} >= {LEADS_THRESHOLD} ✓")
    print(f"  decision_maker='{context['decision_maker']}' ✓")


def test_scenario_2_online_school():
    """
    Тест 2: Онлайн-школа английского
    - 150 заявок/мес
    - decision_maker: "я"
    - возражение: "дороговато"

    Ожидание: SENIOR_MODEL (150 >= 100)
    """
    print("\n=== Тест 2: Онлайн-школа + 'дороговато' ===\n")

    context = {
        "name": "Анна",
        "niche": "Онлайн-школа английского",
        "leads_per_month": 150,
        "channels": "соцсети, реклама",
        "current_process": "менеджеры",
        "pain": "медленные ответы",
        "decision_maker": "self",
    }

    print("Контекст клиента:")
    for k, v in context.items():
        print(f"  {k}: {v}")

    # Шаг 1: main_pitch
    print("\n1. Первый ответ (main_pitch):")
    mode1 = "main_pitch"
    model1 = select_model(mode1, context)
    print(f"   mode={mode1} -> model={model1}")
    assert model1 == BASE_MODEL

    # Шаг 2: Клиент говорит "дороговато"
    user_message = "дороговато как-то"
    print(f"\n2. Клиент: '{user_message}'")

    is_objection = is_price_objection(user_message)
    print(f"   is_price_objection={is_objection}")
    assert is_objection

    mode2 = "price_objection"
    model2 = select_model(mode2, context)
    print(f"   mode={mode2} -> model={model2}")

    # 150 >= 100, поэтому SENIOR_MODEL
    assert model2 == SENIOR_MODEL, "Должен использоваться SENIOR_MODEL (150 >= 100)!"

    print("\n✓ Тест 2 пройден: SENIOR_MODEL активируется")
    print(f"  leads={context['leads_per_month']} >= {LEADS_THRESHOLD} ✓")


def test_scenario_low_leads():
    """
    Тест 3: Мало лидов - должен оставаться BASE_MODEL
    """
    print("\n=== Тест 3: Мало лидов (80) ===\n")

    context = {"leads_per_month": 80, "decision_maker": "self"}

    mode = "price_objection"
    model = select_model(mode, context)
    print(f"mode={mode}, leads=80, dm=self -> {model}")

    assert model == BASE_MODEL, "При < 100 лидах должен быть BASE_MODEL"
    print("\n✓ Тест 3 пройден: BASE_MODEL для малого объема лидов")


def run_all_tests():
    """Запуск всех тестов"""
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ LLM-ОРКЕСТРАТОРА ДЛЯ AI-ПРОДАВЦА")
    print("=" * 60)

    test_price_objection_detection()
    test_model_selection_logic()
    test_scenario_1_building_materials()
    test_scenario_2_online_school()
    test_scenario_low_leads()

    print("\n" + "=" * 60)
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print("=" * 60)

    print("\n📊 Итоговая логика выбора модели:")
    print(f"   BASE_MODEL: {BASE_MODEL}")
    print(f"   SENIOR_MODEL: {SENIOR_MODEL}")
    print(f"   LEADS_THRESHOLD: {LEADS_THRESHOLD}")
    print("\n   SENIOR_MODEL включается когда:")
    print("   1. mode in ('price_objection', 'closing', 'other_objection')")
    print(f"   2. leads_per_month >= {LEADS_THRESHOLD}")
    print("   3. decision_maker in ('self', 'owner', 'founder', ...)")


if __name__ == "__main__":
    run_all_tests()
