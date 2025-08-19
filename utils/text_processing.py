# ~/ai_seller/project/python-core/utils/text_processing.py

"""
Утилиты для предобработки и нормализации текста.
Используются в анализе, валидации и генерации.
"""

import re
import html
from typing import List


def clean_text(text: str) -> str:
    """
    Удаляет HTML-теги, спецсимволы, эмодзи и лишние пробелы.

    :param text: Исходный текст
    :return: Очищенный текст
    """
    if not text:
        return ""

    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)  # HTML
    text = re.sub(r"[^\w\s,.!?@():\"'-]", "", text)  # Эмодзи и мусор
    text = re.sub(r"\s+", " ", text)  # Много пробелов
    return text.strip()


def normalize_text(text: str) -> str:
    """
    Приводит текст к нижнему регистру и удаляет мусор.

    :param text: Исходный текст
    :return: Нормализованный текст
    """
    return clean_text(text).lower()


def extract_keywords(text: str, min_length: int = 4) -> List[str]:
    """
    Наивное извлечение ключевых слов по длине.

    :param text: Входной текст
    :param min_length: Минимальная длина слова
    :return: Список ключевых слов
    """
    words = normalize_text(text).split()
    return [word for word in words if len(word) >= min_length]


def has_question(text: str) -> bool:
    """
    Проверка, содержит ли текст вопрос.

    :param text: Исходный текст
    :return: True, если найден вопрос
    """
    return "?" in text or any(word in text.lower() for word in ["что", 
"как", "когда", "где", "почему", "зачем"])


# Пример локального использования
if __name__ == "__main__":
    raw = "   <b>Здравствуйте!</b> 😃 Меня интересует костюм. Что по 
срокам?"
    print("Исходный:", raw)
    print("Очищенный:", clean_text(raw))
    print("Ключевые слова:", extract_keywords(raw))
    print("Это вопрос?", has_question(raw))

