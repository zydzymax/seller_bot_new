"""
openai_adapter.py — Адаптер OpenAI GPT-4 для SoVAni AI-продавца
© SoVAni 2025
"""

import os
import structlog
from typing import List, Dict, Optional
from openai import AsyncOpenAI
import asyncio

logger = structlog.get_logger("ai_seller.openai_adapter")

class OpenAIAdapter:
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-5"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key must be provided")
        self.model = model
        self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate(self, messages: List[Dict], temperature: float = 0.25, max_tokens: int = 1024) -> str:
        """
        Асинхронно вызывает OpenAI ChatCompletion API
        messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
        """
        try:
            # Параметры для GPT-5
            params = {
                "model": self.model,
                "messages": messages,
                "n": 1,
                "stop": None,
            }
            
            # GPT-5 поддерживает только temperature=1 (по умолчанию)
            if self.model != "gpt-5":
                params["temperature"] = temperature
            
            # GPT-5 требует max_completion_tokens вместо max_tokens
            # Для GPT-5 значительно увеличиваем лимит из-за reasoning токенов (особенно для русского языка)
            if self.model == "gpt-5":
                params["max_completion_tokens"] = max(max_tokens * 8, 1500)
            else:
                params["max_tokens"] = max_tokens
            
            # Логируем точную модель которая отправляется
            logger.info("OpenAI request", model=self.model, message_count=len(messages))
            
            response = await self.client.chat.completions.create(**params)
            reply = response.choices[0].message.content
            
            # Логируем подробности ответа для отладки GPT-5
            logger.info("OpenAI response OK", 
                       length=len(reply) if reply else 0,
                       finish_reason=response.choices[0].finish_reason,
                       usage=response.usage.model_dump() if response.usage else None)
            
            return reply.strip() if reply else ""
        except Exception as e:
            logger.error("openai_adapter_error", error=str(e), model=self.model, source="OpenAIAdapter")
            return "⚠️ OpenAI временно недоступен. Ответ будет позже."

# Для ручного теста (опционально)
if __name__ == "__main__":
    import asyncio
    async def _test():
        oa = OpenAIAdapter()
        resp = await oa.generate([
            {"role": "system", "content": "Ты — эксперт по трикотажу."},
            {"role": "user", "content": "Здравствуйте, мне нужен расчёт партии футболок"}
        ])
        print("OpenAI:", resp)
    asyncio.run(_test())
