"""
entity_extractor.py
📌 Извлечение ключевых параметров из сообщений пользователя (пошив).
"""

import re
from typing import Dict


def extract_lead_info(text: str) -> Dict[str, str]:
    """
    Выделяет ключевые параметры из пользовательского сообщения:
    - продукт
    - количество/партия
    - формат (давальческий / под ключ)
    - наличие ТЗ
    - наличие лекал
    - материал (если указан)
    - признак: знает ли клиент материал

    Args:
        text (str): Входящее сообщение пользователя

    Returns:
        Dict[str, str]: Словарь с извлечёнными параметрами
    """
    result = {
        "product": None,
        "quantity": None,
        "format": None,
        "tech_spec": None,
        "patterns": None,
        "material": None,
        "material_known": None,
    }

    text_lower = text.lower()

    # --- Продукт
    product_match = re.search(
        r"(пижам[аиы]|футболк[аиы]|лонгслив[аиы]|шорт[ыа]|брюк[иа]|свитшот[ыа])",
        text_lower,
    )
    if product_match:
        result["product"] = product_match.group(1)

    # --- Количество / партия
    qty_match = re.search(r"(\d{2,5})\s?(шт|штук|ед|единиц|парт\w*)", text_lower)
    if qty_match:
        result["quantity"] = qty_match.group(1)

    # --- Формат
    if "под ключ" in text_lower:
        result["format"] = "под ключ"
    elif "давальческ" in text_lower or "своё сырьё" in text_lower:
        result["format"] = "давальческий"

    # --- Техзадание
    if re.search(r"(тех\.?задани[ея]|тз|техническое задание)", text_lower):
        result["tech_spec"] = "есть"

    # --- Лекала
    if re.search(r"(лекал[аоие]?|выкро[йе]к[аи]?)", text_lower):
        result["patterns"] = "есть"

    # --- Материалы (по ключевым словам)
    material_keywords = [
        "кулир",
        "футер",
        "интерлок",
        "рибана",
        "вискоза",
        "хлопок",
        "полиэстер",
        "эластан",
        "бифлекс",
        "трикотаж",
        "ситец",
        "поплин",
        "ткань",
        "материал",
        "трикотажный",
    ]
    for word in material_keywords:
        if word in text_lower:
            result["material"] = word
            result["material_known"] = True
            break
    else:
        # Если ничего не нашли, но формат — давальческий → надо уточнять
        if result["format"] == "давальческий":
            result["material_known"] = False

    return result
