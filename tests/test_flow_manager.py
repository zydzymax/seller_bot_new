"""
test_flow_manager.py — Тесты для FSM и flow manager SoVAni AI-продавца.

© SoVAni 2025
"""

import pytest

from dialog.flow_manager import (
    FlowState,
    DialogContext,
    BusinessRulesValidator,
)


@pytest.fixture
def business_rules():
    """Фикстура с бизнес-правилами для тестов"""
    return {
        "moq": {"turnkey_per_color": 1000},
        "scheduling": {"promise_exact_dates": False, "allow_partial_shipments": True},
        "pricing": {
            "push_for_budget_if_client_refuses": False,
            "mode_default": "factory_quote",
        },
    }


@pytest.fixture
def rules_validator(business_rules):
    """Фикстура для валидатора бизнес-правил"""
    return BusinessRulesValidator(business_rules)


class TestBusinessRulesValidator:
    """Тесты для валидатора бизнес-правил"""

    def test_validate_moq_success(self, rules_validator):
        """Тест успешной валидации MOQ"""
        result = rules_validator.validate_moq(total_qty=5000, colors_count=3)

        assert result["valid"] is True
        assert result["quantity_per_color"] == 1666  # 5000 // 3

    def test_validate_moq_violation(self, rules_validator):
        """Тест нарушения MOQ"""
        result = rules_validator.validate_moq(total_qty=1500, colors_count=3)

        assert result["valid"] is False
        assert result["error"] == "moq_violation"
        assert "500 шт/цвет" in result["message"]  # 1500 // 3 = 500
        assert len(result["suggestions"]) == 3


class TestDialogContext:
    """Тесты для DialogContext"""

    def test_dialog_context_creation(self):
        """Тест создания DialogContext"""
        context = DialogContext(
            user_id=12345,
            chat_id=67890,
            product_type="толстовка",
            total_quantity=3000,
            colors_count=2,
        )

        assert context.user_id == 12345
        assert context.chat_id == 67890
        assert context.product_type == "толстовка"
        assert context.total_quantity == 3000
        assert context.colors_count == 2
        assert context.current_state == FlowState.GREETING
        assert context.warnings == []
