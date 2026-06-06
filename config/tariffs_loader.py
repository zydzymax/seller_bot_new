"""
Tariffs Loader - загрузка и работа с конфигом тарифов SalesWhisper

Единый источник правды по ценам для бота и сайта.
"""

import os
import yaml
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

# Путь к конфигу тарифов
TARIFFS_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "saleswhisper_tariffs.yaml"
)


@dataclass
class Tariff:
    """Тариф SalesWhisper"""

    id: str
    name: str
    price_min: int
    price_max: Optional[int]
    label: str
    short_desc: str
    features: List[str]
    suitable_for: str
    is_popular: bool = False

    def format_price(self) -> str:
        """Форматированная цена для отображения"""
        if self.price_max is None:
            return f"от {self.price_min:,} ₽".replace(",", " ")
        elif self.price_min == self.price_max:
            return f"{self.price_min:,} ₽".replace(",", " ")
        else:
            return f"{self.price_min:,}–{self.price_max:,} ₽".replace(",", " ")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "label": self.label,
            "short_desc": self.short_desc,
            "features": self.features,
            "suitable_for": self.suitable_for,
            "is_popular": self.is_popular,
        }


class TariffsConfig:
    """Конфигурация тарифов SalesWhisper"""

    def __init__(self, config_path: str = TARIFFS_CONFIG_PATH):
        self.config_path = config_path
        self._config = self._load_config()
        self._tariffs = self._parse_tariffs()

    def _load_config(self) -> Dict[str, Any]:
        """Загрузить конфиг из YAML"""
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _parse_tariffs(self) -> List[Tariff]:
        """Распарсить тарифы в объекты"""
        tariffs = []
        for t in self._config.get("tariffs", []):
            tariffs.append(
                Tariff(
                    id=t["id"],
                    name=t["name"],
                    price_min=t["price_min"],
                    price_max=t.get("price_max"),
                    label=t["label"],
                    short_desc=t.get("short_desc", ""),
                    features=t.get("features", []),
                    suitable_for=t.get("suitable_for", ""),
                    is_popular=t.get("is_popular", False),
                )
            )
        return tariffs

    @property
    def min_price(self) -> int:
        """Минимальная стоимость внедрения"""
        return self._config.get("min_price", 25000)

    @property
    def tariffs(self) -> List[Tariff]:
        """Список всех тарифов"""
        return self._tariffs

    def get_tariff(self, tariff_id: str) -> Optional[Tariff]:
        """Получить тариф по ID"""
        for t in self._tariffs:
            if t.id == tariff_id:
                return t
        return None

    def get_tariff_by_leads(self, leads_per_month: int) -> Tariff:
        """Рекомендовать тариф по объёму заявок"""
        recommendations = self._config.get("bot_rules", {}).get(
            "tariff_recommendations", []
        )

        for rec in recommendations:
            max_leads = rec.get("leads_max")
            if max_leads is None or leads_per_month <= max_leads:
                tariff = self.get_tariff(rec["tariff_id"])
                if tariff:
                    return tariff

        # Fallback - последний тариф (самый дорогой)
        return self._tariffs[-1] if self._tariffs else None

    def get_tariff_by_budget(self, budget: int) -> Optional[Tariff]:
        """Подобрать тариф под бюджет"""
        if budget < self.min_price:
            return None

        # Находим подходящий тариф
        suitable = None
        for t in self._tariffs:
            if t.price_min <= budget:
                suitable = t
            else:
                break

        return suitable

    def get_low_budget_response(self) -> str:
        """Ответ при бюджете ниже минимального"""
        return self._config.get("bot_rules", {}).get(
            "low_budget_response",
            "К сожалению, минимальная стоимость внедрения ИИ-продавца — 25 000 ₽.",
        )

    def format_tariffs_for_prompt(self) -> str:
        """Форматировать тарифы для системного промпта"""
        lines = []
        for i, t in enumerate(self._tariffs, 1):
            lines.append(f"{i}) «{t.name}» — {t.format_price()}")
            for feature in t.features:
                lines.append(f"   – {feature}")
            lines.append("")

        return "\n".join(lines)

    def format_tariffs_short(self) -> str:
        """Краткий список тарифов"""
        return "\n".join([f"• {t.name}: {t.format_price()}" for t in self._tariffs])


# Глобальный инстанс (singleton)
_tariffs_config: Optional[TariffsConfig] = None


def get_tariffs_config() -> TariffsConfig:
    """Получить конфиг тарифов (singleton)"""
    global _tariffs_config
    if _tariffs_config is None:
        _tariffs_config = TariffsConfig()
    return _tariffs_config


def get_min_price() -> int:
    """Получить минимальную цену"""
    return get_tariffs_config().min_price


def get_tariffs() -> List[Tariff]:
    """Получить список тарифов"""
    return get_tariffs_config().tariffs


def recommend_tariff(
    leads_per_month: int = None, budget: int = None
) -> Optional[Tariff]:
    """Рекомендовать тариф по параметрам"""
    config = get_tariffs_config()

    if budget is not None:
        return config.get_tariff_by_budget(budget)
    elif leads_per_month is not None:
        return config.get_tariff_by_leads(leads_per_month)

    return config.tariffs[0]  # Default: Start
