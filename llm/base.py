"""
base.py — расширенный интерфейс и структуры для LLM-провайдеров SoVAni.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Callable

class ModelType(str, Enum):
    GPT_4_TURBO = "gpt-4-turbo"
    GPT_4 = "gpt-4"
    GPT_35_TURBO = "gpt-3.5-turbo"
    CLAUDE_3_OPUS = "claude-3-opus"
    CLAUDE_3_SONNET = "claude-3-sonnet"

    @classmethod
    def register_model(cls, name: str):
        """Регистрация новой модели динамически (если надо)"""
        cls._member_map_[name.upper()] = name.lower()
        return name.lower()

@dataclass
class LLMRequest:
    prompt: str
    model: ModelType
    history: Optional[List[Dict[str, str]]] = None
    system_prompt: Optional[str] = None
    max_tokens: int = 1000
    temperature: float = 0.7
    metadata: Optional[Dict[str, Any]] = None
    version: str = "v1"

    def validate(self) -> None:
        if not isinstance(self.prompt, str) or len(self.prompt) < 2:
            raise ValueError("Prompt is too short or invalid")
        if self.max_tokens < 10 or self.max_tokens > 4096:
            raise ValueError("max_tokens out of range")
        # Можно добавить больше проверок

@dataclass
class LLMError:
    code: str
    message: str

@dataclass
class LLMResponse:
    content: str
    model: ModelType
    provider: str
    usage: Dict[str, int]
    latency_ms: float
    cached: bool = False
    error: Optional[LLMError] = None
    quality: Optional[float] = None  # опционально, если будем оценивать
    version: str = "v1"

class LLMProvider(ABC):
    """
    Абстрактный интерфейс для любого LLM-провайдера.
    """

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """
        Асинхронная генерация полного ответа.
        """
        pass

    async def stream_generate(self, request: LLMRequest, on_chunk: Optional[Callable[[str], None]] = None) -> LLMResponse:
        """
        (Необязательно) Потоковая генерация для LLM с поддержкой streaming.
        По умолчанию NotImplemented.
        """
        raise NotImplementedError("Streaming не реализован для данного провайдера.")

    @abstractmethod
    def calculate_cost(self, usage: Dict[str, int]) -> float:
        """
        Подсчёт стоимости вызова для аналитики.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Доступность провайдера для работы.
        """
        pass

