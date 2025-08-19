"""
exceptions.py — исключения для FSM, flow_manager и диалоговой логики SoVAni.
"""

class FlowTimeout(Exception):
    pass

class FlowValidationError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

class FlowStorageError(Exception):
    pass

class VersionConflictError(Exception):
    pass

class FlowInitializationError(Exception):
    pass

