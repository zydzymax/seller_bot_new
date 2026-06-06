import re
import yaml
import os
from typing import Literal

# Load tone rules
TONE_PATH = os.path.join(os.path.dirname(__file__), "..", "dialog", "tone_rules.yaml")
with open(TONE_PATH, "r", encoding="utf-8") as f:
    TONE = yaml.safe_load(f)

FILLERS = ["итак", "смотрите", "типа", "ну", "короче", "вообще", "прям", "реально"]


def shape(text: str, kind: Literal["ask", "confirm", "summary"]) -> str:
    """
    Форматирует сообщение согласно tone rules
    """
    if not text:
        return ""

    # Удаляем филлеры
    for filler in FILLERS:
        text = re.sub(rf"\b{filler}\b", "", text, flags=re.IGNORECASE)

    # Удаляем повторяющиеся куски (3+ одинаковых слов подряд)
    words = text.split()
    cleaned_words = []
    i = 0
    while i < len(words):
        if (
            i + 2 < len(words)
            and words[i].lower() == words[i + 1].lower() == words[i + 2].lower()
        ):
            # Нашли повтор из 3+ слов, берем только одно
            cleaned_words.append(words[i])
            # Пропускаем все последующие повторы
            current_word = words[i].lower()
            i += 1
            while i < len(words) and words[i].lower() == current_word:
                i += 1
        else:
            cleaned_words.append(words[i])
            i += 1

    text = " ".join(cleaned_words).strip()

    # Обрезаем по лимиту длины
    max_chars = TONE["style"]["max_chars"]
    if len(text) > max_chars:
        # Обрезаем по последнему предложению, чтобы не порвать мысль
        sentences = re.split(r"[.!?]+", text[:max_chars])
        if len(sentences) > 1:
            text = ". ".join(sentences[:-1]) + "."
        else:
            text = text[:max_chars].rsplit(" ", 1)[0]

    # Управление эмодзи
    emoji_policy = TONE["style"]["emoji_policy"]
    emoji_set = TONE["style"]["emoji_set"]

    if emoji_policy == "confirm_only":
        if kind == "confirm":
            # В подтверждениях разрешён один эмодзи в конце
            if not any(emoji in text for emoji in emoji_set):
                text += " ✅"
        else:
            # В других типах удаляем все эмодзи
            for emoji in emoji_set + ["😊", "👌", "🔥", "💪", "🚀", "📋", "❌"]:
                text = text.replace(emoji, "")

    # Нормализуем пробелы
    text = re.sub(r"\s+", " ", text).strip()

    return text
