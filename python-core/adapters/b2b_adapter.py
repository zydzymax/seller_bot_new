# ~/ai_seller/project/python-core/adapters/b2b_adapter.py

"""
Адаптер под B2B-коммуникацию для AI-продавца.
Изменяет стиль ответов, добавляет деловые аргументы, убирает лишние 
эмоции.
"""

from typing import Dict


class B2BAdapter:
    """
    Класс преобразования фраз и логики под B2B-аудиторию (директора, 
закупщики, байеры).
    """

    def __init__(self):
        # Аргументы, которые усиливают доверие и логику B2B-переговоров
        self.key_arguments = [
            "Мы обеспечиваем стабильные отгрузки и чёткие сроки.",
            "Все изделия проходят проверку качества на каждом этапе.",
            "Готовы работать партиями от 50 до 10 000 единиц под ваш 
график.",
            "Работаем по договору. Возможны отсрочки и постоплата для 
надёжных партнёров."
        ]

    def adapt_text(self, response: str) -> str:
        """
        Добавляет аргументы, устраняет мягкие формулировки, усиливает 
деловитость.

        :param response: Исходный текст от LLM
        :return: Адаптированный текст
        """
        clean = response.strip()

        # Замена излишне мягких слов
        replacements = {
            "дорогой": "уважаемый",
            "милая": "",
            "душевно": "надежно",
            "уютно": "функционально",
            "мы очень стараемся": "мы гарантируем качество",
            "будем рады": "готовы обсудить условия",
        }

        for old, new in replacements.items():
            clean = clean.replace(old, new)

        # Добавим 1–2 аргумента для усиления
        additions = "\n\n" + "\n".join(self.key_arguments[:2])
        return clean + additions

    def adapt_persona(self, persona: Dict[str, str]) -> Dict[str, str]:
        """
        Адаптирует словарь персоны под деловой стиль.

        :param persona: Описание ИИ-продавца
        :return: Адаптированная персона
        """
        persona["tone"] = "деловой"
        persona["style"] = "уверенный, аргументированный, краткий"
        persona["signature"] = "С уважением, отдел B2B-партнёрств"
        return persona


# Пример использования
if __name__ == "__main__":
    adapter = B2BAdapter()
    original = "Здравствуйте, милая! Мы очень стараемся, чтобы вам было 
уютно."
    adapted = adapter.adapt_text(original)
    print("До:\n", original)
    print("\nПосле:\n", adapted)

