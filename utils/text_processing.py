import re

NBSP = "\u00A0"
# числа, разделители, плавающая часть (для 3,5k), суффиксы k/к/тыс/pcs/шт
QTY_FULL_RE = re.compile(
    r'(?<!\d)\s*([~≈]?\s*\d+(?:[.,]\d+)?(?:[ '+NBSP+']\d{3})*|\d+(?:[ \.,'+NBSP+']\d{3})*)\s*(к|k|тыс\.?|тыс|тыщи)?\s*(шт|pcs)?',
    re.IGNORECASE
)

def parse_quantity(text: str) -> int | None:
    """
    Понимает: 4000; 4 000; 5,000; ≈5000; ~3 200; 3,5k; 4k; 4к; 4 тыс; 4000 шт; 5000pcs.
    'k/к/тыс' → *1000, с учётом десятичной части (3,5k → 3500).
    Отсекает неадекватные величины > 50 млн.
    """
    if not text: return None
    
    # Найти все совпадения и выбрать лучший вариант
    matches = list(QTY_FULL_RE.finditer(text))
    if not matches: return None
    
    # Приоритет: числа с суффиксами k/к/тыс, потом большие числа (>100), потом любые
    best_match = None
    best_priority = -1
    
    for match in matches:
        num_raw, suf, _ = match.groups()
        clean = re.sub(r'[~≈\s'+NBSP+r']', '', num_raw).replace(',', '.')
        try:
            val = float(clean)
        except ValueError:
            continue
            
        priority = 0
        if suf and re.match(r'^(к|k|тыс)', suf, re.I):
            priority = 3  # Highest priority for k/к/тыс
            val *= 1000.0
        elif val >= 1000:
            priority = 2  # High priority for large numbers
        elif val >= 10:
            priority = 1  # Medium priority for medium numbers
        
        iv = int(round(val))
        if 1 <= iv <= 50_000_000 and priority > best_priority:
            best_match = iv
            best_priority = priority
    
    return best_match