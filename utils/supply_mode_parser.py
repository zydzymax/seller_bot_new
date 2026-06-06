import re

MODES = {
    "pod_kluch": [
        r"\bпод\s*ключ\b",
        r"\bполный\s*цикл\b",
        r"\bвсё\s*под\s*ключ\b",
        r"\bиз\s*наших\s*материалов\b",
        r"\bмы\s*закупим\b",
        r"\bбез\s*ваших\s*материалов\b",
        r"\bзакупаете\s*сами\b",
        r"\bсами\s*закупаете\b",
    ],
    "davalskoe": [
        r"\bдавальческ(ое|ий|ого)\s*(сырь(ё|е)|режим)?\b",
        r"\bиз\s*(моих|своих)\s*материалов\b",
        r"\bваше\s*сырь(ё|е)\b",
        r"\bматериал\s*клиента\b",
    ],
}


def parse_supply_mode(text: str) -> str | None:
    if not text:
        return None
    low = text.lower()
    for mode, pats in MODES.items():
        for p in pats:
            if re.search(p, low, re.IGNORECASE):
                return mode
    # точные варианты
    if low.strip() in {"под ключ", "подключ", "под-ключ"}:
        return "pod_kluch"
    if low.strip() in {"давальческое сырьё", "давальческое сырье", "давальческое"}:
        return "davalskoe"
    return None
