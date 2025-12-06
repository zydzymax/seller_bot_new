import re
from typing import Dict, Optional
from utils.text_processing import parse_quantity

PRODUCTS = {
    "футболка": ["футболк", "майк", "тишка", "t-shirt"],
    "худи": ["худи", "толстовк", "hoodie"],
    "лонгслив": ["лонгслив", "longsleeve", "длинный рукав"],
    "поло": ["поло", "polo"],
    "свитшот": ["свитшот", "sweatshirt"],
    "бомбер": ["бомбер", "bomber"],
    "жилет": ["жилет", "vest"]
}

SUPPLY_MODES = {
    "под ключ": ["под ключ", "ваши материалы", "вы закупаете", "закупаете"],
    "давальческое сырьё": ["давальческое", "наше сырье", "наши материалы", "привезем материал", "наш материал"]
}

def extract_slots(text: str) -> Dict[str, Optional[str]]:
    """Извлекает слоты из свободного текста пользователя"""
    text_lower = text.lower()
    slots = {}
    
    # Product type
    for product, patterns in PRODUCTS.items():
        if any(pattern in text_lower for pattern in patterns):
            slots["product"] = product
            break
    
    # Supply mode
    for mode, patterns in SUPPLY_MODES.items():
        if any(pattern in text_lower for pattern in patterns):
            slots["supply_mode"] = mode
            break
    
    # Quantity first (to avoid conflicts with color numbers)
    qty = parse_quantity(text)
    if qty:
        slots["qty"] = qty
    
    # Colors (число) - but not if it conflicts with quantity
    color_match = re.search(r'(\d+)\s*цвет', text_lower)
    if color_match:
        colors = int(color_match.group(1))
        if 1 <= colors <= 10:
            # Only set colors if it's different from quantity
            if not qty or colors != qty:
                slots["colors"] = colors
    
    # Deadline
    deadline_patterns = [
        r'к\s+(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)',
        r'за\s+(\d+)\s+(недел|день|месяц)',
        r'до\s+(\d{1,2}\.\d{1,2})',
        r'срочно|быстро|как можно скорее'
    ]
    for pattern in deadline_patterns:
        if re.search(pattern, text_lower):
            slots["deadline"] = re.search(pattern, text_lower).group(0)
            break
    
    # Logo/printing
    logo_patterns = ["логотип", "печать", "нанесение", "принт", "вышивка", "без лого"]
    if any(pattern in text_lower for pattern in logo_patterns):
        if "без" in text_lower and ("лого" in text_lower or "нанесен" in text_lower):
            slots["logo"] = "нет"
        else:
            slots["logo"] = "есть"
    
    return slots

def fastlane_decision(slots: Dict, required_slots: list = ["product", "supply_mode", "qty"]) -> bool:
    """Решает, можно ли использовать fast-path"""
    filled_key_slots = sum(1 for slot in required_slots if slots.get(slot))
    return filled_key_slots >= 3  # Если заполнено >= 3 ключевых слотов