"""
context_manager.py — базовый диалоговый контекст для FSM SoVAni.
"""

class DialogContext:
    """
    Контекст диалога пользователя.
    Можно расширять (user_id, текущее состояние, история и др.)
    """
    def __init__(self, user_id: int, state=None):
        self.user_id = user_id
        self.state = state

    @classmethod
    def init_new(cls, user_id: int):
        return cls(user_id=user_id, state=None)

