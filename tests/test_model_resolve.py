"""
test_model_resolve.py — Unit тесты для резолвера моделей

© SoVAni 2025
"""

import pytest
from llm.orchestrator import resolve_model, MODEL_MAP


def test_resolve_valid_models():
    """Тест резолвера для валидных моделей"""
    # Прямые модели
    assert resolve_model("gpt-4o") == ("openai", "gpt-4o")
    assert resolve_model("gpt-4-turbo") == ("openai", "gpt-4-turbo")
    assert resolve_model("claude-opus") == ("anthropic", "claude-3-opus-20240229")
    
    # Алиасы
    assert resolve_model("gpt4t") == ("openai", "gpt-4-turbo")
    assert resolve_model("gpt4") == ("openai", "gpt-4-turbo")


def test_resolve_invalid_model():
    """Тест резолвера для неизвестных моделей"""
    with pytest.raises(ValueError) as exc_info:
        resolve_model("unknown-model")
    
    assert "Неизвестная модель 'unknown-model'" in str(exc_info.value)
    assert "Доступны:" in str(exc_info.value)


def test_resolve_empty_model():
    """Тест резолвера для пустой строки"""
    with pytest.raises(ValueError):
        resolve_model("")


def test_model_map_coverage():
    """Тест полноты карты моделей"""
    # Проверяем, что все ожидаемые модели есть
    expected_models = [
        "gpt-4o", "gpt-4-turbo", "gpt-5", "gpt-5-mini", "gpt-5-nano",
        "claude-opus", "claude-haiku", "gpt4t", "gpt4"
    ]
    
    for model in expected_models:
        assert model in MODEL_MAP
        provider, model_id = MODEL_MAP[model]
        assert provider in ["openai", "anthropic"]
        assert len(model_id) > 0


def test_resolve_gpt5_series():
    """Тест резолвера для GPT-5 серии"""
    assert resolve_model("gpt-5") == ("openai", "gpt-5")
    assert resolve_model("gpt-5-mini") == ("openai", "gpt-5-mini")
    assert resolve_model("gpt-5-nano") == ("openai", "gpt-5-nano")


def test_resolve_anthropic_models():
    """Тест резолвера для Anthropic моделей"""
    assert resolve_model("claude-opus") == ("anthropic", "claude-3-opus-20240229")
    assert resolve_model("claude-haiku") == ("anthropic", "claude-3-haiku-20240307")


if __name__ == "__main__":
    pytest.main([__file__])