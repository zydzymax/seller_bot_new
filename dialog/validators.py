"""
validators.py — базовые валидаторы для сообщений пользователя и FSM.
"""

def validate_input(message: str, state=None):
    """
    Базовая валидация пользовательского ввода для FSM.
    Возвращает (is_valid: bool, error_msg: str)
    """
    if not isinstance(message, str) or not message.strip():
        return False, "Пустое или невалидное сообщение"
    if len(message) > 1024:
        return False, "Сообщение слишком длинное"
    # Можно добавить доп. проверки под свою FSM
    return True, ""

