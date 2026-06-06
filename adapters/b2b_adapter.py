"""
Адаптер под B2B-коммуникацию для AI-продавца.
"""

from typing import Dict


class B2BAdapter:
    """Преобразует ответы AI в более деловой стиль для B2B-аудитории."""

    def __init__(self) -> None:
        self.key_arguments = [
            "Мы обеспечиваем стабильные отгрузки и четкие сроки.",
            "Все изделия проходят проверку качества на каждом этапе.",
            "Готовы работать партиями от 50 до 10 000 единиц под ваш график.",
            "Работаем по договору. Возможны отсрочки и постоплата для надежных партнеров.",
        ]

    def adapt_text(self, response: str) -> str:
        """Убирает излишне мягкие формулировки и усиливает деловой тон."""
        clean = response.strip()

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

        additions = "\n\n" + "\n".join(self.key_arguments[:2])
        return clean + additions

    def adapt_persona(self, persona: Dict[str, str]) -> Dict[str, str]:
        """Обновляет описание персоны под B2B-стиль коммуникации."""
        persona["tone"] = "деловой"
        persona["style"] = "уверенный, аргументированный, краткий"
        persona["signature"] = "С уважением, отдел B2B-партнерств"
        return persona


if __name__ == "__main__":
    adapter = B2BAdapter()
    original = "Здравствуйте, милая! Мы очень стараемся, чтобы вам было уютно."
    adapted = adapter.adapt_text(original)
    print("До:\n", original)
    print("\nПосле:\n", adapted)
