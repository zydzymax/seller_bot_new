"""
Совместимость для старого импорта `adapters.crm_adapter_new`.

В проекте рабочая реализация находится в `adapters.crm_adapter`.
Этот модуль сохраняет обратную совместимость без дублирования логики.
"""

from adapters.crm_adapter import (  # noqa: F401
    CRMAdapter,
    ContactData,
    OrderDetails,
    LeadStatus,
    get_crm_adapter,
)

__all__ = [
    "CRMAdapter",
    "ContactData",
    "OrderDetails",
    "LeadStatus",
    "get_crm_adapter",
]
