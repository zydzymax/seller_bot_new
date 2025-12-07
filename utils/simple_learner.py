"""
Simple Learning System
Простая система обучения: выбор более успешных вариантов с exploration
"""

import random
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import json
import time


@dataclass
class Variant:
    """Вариант (реплика, порядок действий и т.д.)"""
    variant_id: str
    content: str

    # Metrics
    trials: int = 0
    successes: int = 0

    # Timestamps
    created_at: float = field(default_factory=time.time)
    last_used_at: Optional[float] = None

    @property
    def success_rate(self) -> float:
        """Процент успеха"""
        if self.trials == 0:
            return 0.0
        return self.successes / self.trials

    @property
    def confidence(self) -> float:
        """Уверенность (на основе количества попыток)"""
        # Чем больше trials, тем выше confidence
        return min(1.0, self.trials / 30)  # Макс confidence при 30+ trials


@dataclass
class VariantGroup:
    """Группа вариантов для одного контекста (напр., greeting для textile)"""
    context: str  # "greeting:textile", "objection_price:ai_seller"
    variants: List[Variant] = field(default_factory=list)

    # Hyperparameters
    epsilon: float = 0.2  # 20% exploration (тестирование новых вариантов)
    min_trials: int = 5   # Минимум попыток перед выводами

    def add_variant(self, variant: Variant):
        """Добавить вариант"""
        self.variants.append(variant)

    def select_variant(self) -> Variant:
        """
        Выбрать вариант (epsilon-greedy)

        С вероятностью epsilon выбираем случайный (exploration)
        С вероятностью (1-epsilon) выбираем лучший (exploitation)
        """
        if not self.variants:
            raise ValueError("No variants available")

        # Exploration: выбираем случайный
        if random.random() < self.epsilon:
            return random.choice(self.variants)

        # Exploitation: выбираем лучший с учетом confidence

        # Если все варианты < min_trials, выбираем равномерно
        if all(v.trials < self.min_trials for v in self.variants):
            return random.choice(self.variants)

        # Иначе выбираем с максимальным success_rate * confidence
        best_variant = max(
            self.variants,
            key=lambda v: v.success_rate * v.confidence
        )

        return best_variant

    def record_outcome(self, variant_id: str, success: bool):
        """
        Записать результат использования варианта

        Args:
            variant_id: ID варианта
            success: True если успех, False если провал
        """
        for variant in self.variants:
            if variant.variant_id == variant_id:
                variant.trials += 1
                if success:
                    variant.successes += 1
                variant.last_used_at = time.time()
                break

    def get_stats(self) -> List[Dict]:
        """Получить статистику по вариантам"""
        stats = []
        for v in sorted(self.variants, key=lambda x: x.success_rate, reverse=True):
            stats.append({
                'variant_id': v.variant_id,
                'content': v.content[:50] + '...' if len(v.content) > 50 else v.content,
                'trials': v.trials,
                'successes': v.successes,
                'success_rate': v.success_rate,
                'confidence': v.confidence
            })
        return stats


class SimpleLearner:
    """
    Простая система обучения

    Для каждого контекста (этап диалога, тип возражения и т.д.)
    хранит варианты фраз/действий и выбирает более успешные
    """

    def __init__(self, storage_path: str = "learner_data.json"):
        self.storage_path = storage_path
        self.variant_groups: Dict[str, VariantGroup] = {}
        self._load()

    def add_variants(self, context: str, variants: List[Tuple[str, str]]):
        """
        Добавить варианты для контекста

        Args:
            context: Контекст (напр., "greeting:ai_seller_self")
            variants: List of (variant_id, content)
        """
        if context not in self.variant_groups:
            self.variant_groups[context] = VariantGroup(context=context)

        group = self.variant_groups[context]

        for variant_id, content in variants:
            # Проверяем что такого variant_id еще нет
            if not any(v.variant_id == variant_id for v in group.variants):
                group.add_variant(Variant(
                    variant_id=variant_id,
                    content=content
                ))

    def select_variant(self, context: str) -> Optional[Tuple[str, str]]:
        """
        Выбрать вариант для контекста

        Args:
            context: Контекст

        Returns:
            (variant_id, content) или None если вариантов нет
        """
        if context not in self.variant_groups:
            return None

        group = self.variant_groups[context]
        variant = group.select_variant()

        return (variant.variant_id, variant.content)

    def record_outcome(self, context: str, variant_id: str, success: bool):
        """
        Записать результат

        Args:
            context: Контекст
            variant_id: ID использованного варианта
            success: Успех или провал
        """
        if context not in self.variant_groups:
            return

        group = self.variant_groups[context]
        group.record_outcome(variant_id, success)

        # Периодически сохраняем
        if random.random() < 0.1:  # 10% шанс сохранения
            self.save()

    def get_stats(self, context: Optional[str] = None) -> Dict:
        """
        Получить статистику

        Args:
            context: Конкретный контекст или None для всех

        Returns:
            Dict со статистикой
        """
        if context:
            if context in self.variant_groups:
                return {
                    context: self.variant_groups[context].get_stats()
                }
            return {}

        # Все контексты
        stats = {}
        for ctx, group in self.variant_groups.items():
            stats[ctx] = group.get_stats()

        return stats

    def save(self):
        """Сохранить в файл"""
        data = {}
        for context, group in self.variant_groups.items():
            data[context] = {
                'variants': [
                    {
                        'variant_id': v.variant_id,
                        'content': v.content,
                        'trials': v.trials,
                        'successes': v.successes,
                        'created_at': v.created_at,
                        'last_used_at': v.last_used_at
                    }
                    for v in group.variants
                ],
                'epsilon': group.epsilon,
                'min_trials': group.min_trials
            }

        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        """Загрузить из файла"""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for context, group_data in data.items():
                group = VariantGroup(
                    context=context,
                    epsilon=group_data.get('epsilon', 0.2),
                    min_trials=group_data.get('min_trials', 5)
                )

                for v_data in group_data['variants']:
                    variant = Variant(
                        variant_id=v_data['variant_id'],
                        content=v_data['content'],
                        trials=v_data.get('trials', 0),
                        successes=v_data.get('successes', 0),
                        created_at=v_data.get('created_at', time.time()),
                        last_used_at=v_data.get('last_used_at')
                    )
                    group.add_variant(variant)

                self.variant_groups[context] = group

        except FileNotFoundError:
            pass  # Файл еще не создан


# Global instance
_learner = None


def get_learner() -> SimpleLearner:
    """Get global learner instance"""
    global _learner
    if _learner is None:
        _learner = SimpleLearner()
    return _learner


# Example usage
if __name__ == "__main__":
    learner = SimpleLearner("test_learner.json")

    # Добавляем варианты приветствия
    learner.add_variants("greeting:ai_seller_self", [
        ("greet_v1", "Привет! Я Алекс, помогаю автоматизировать продажи. Чем занимаетесь?"),
        ("greet_v2", "Здравствуйте! Меня зовут Алекс. Расскажите о вашем бизнесе?"),
        ("greet_v3", "Добрый день! Помогаю внедрять AI-ботов для продаж. Ваша ниша?")
    ])

    # Симуляция использования
    for i in range(100):
        variant_id, content = learner.select_variant("greeting:ai_seller_self")

        # Симулируем результат (v1 успешнее)
        if variant_id == "greet_v1":
            success = random.random() < 0.7  # 70% успех
        elif variant_id == "greet_v2":
            success = random.random() < 0.5  # 50% успех
        else:
            success = random.random() < 0.3  # 30% успех

        learner.record_outcome("greeting:ai_seller_self", variant_id, success)

    # Статистика
    print(json.dumps(learner.get_stats("greeting:ai_seller_self"), indent=2, ensure_ascii=False))

    learner.save()
