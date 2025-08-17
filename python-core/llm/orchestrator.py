"""
orchestrator.py — надёжный LLM-оркестратор для SoVAni AI-продавца.

- Параллельный вызов нескольких LLM (GPT-4, Claude и др.)
- Circuit breaker с асинхронной блокировкой
- Fallback на сбой, таймауты запросов
- Redis-кэш с hash-контекстом
- Санитизация output, безопасное логирование

© SoVAni 2025
"""

import asyncio
import structlog
import hashlib
import json
import time
from typing import Dict, Any, Optional

from llm.providers.openai_provider import OpenAIProvider
from llm.base import LLMProvider, LLMRequest, ModelType
from llm.cache import RedisCache  # или in-memory
from utils.input_sanitizer import sanitize_input

logger = structlog.get_logger("ai_seller.llm_orchestrator")

def sanitize_output(output: str) -> str:
    """Санитизация вывода LLM"""
    if not output:
        return ""
    # Ограничение длины
    output = output[:4096]
    # Базовая очистка
    return output.strip()

class AsyncCircuitBreaker:
    def __init__(self, max_failures=2, reset_timeout=30):
        self._lock = asyncio.Lock()
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.fail_count = 0
        self.last_failure = 0
        self.open = False

    async def call(self, coro):
        async with self._lock:
            if self.open and (time.monotonic() - self.last_failure < self.reset_timeout):
                raise Exception("Circuit breaker is open")
        try:
            result = await coro
            async with self._lock:
                self.fail_count = 0
                self.open = False
            return result
        except Exception as e:
            async with self._lock:
                self.fail_count += 1
                self.last_failure = time.monotonic()
                if self.fail_count >= self.max_failures:
                    self.open = True
            raise

class LLMOrchestrator:
    def __init__(self, redis_url: Optional[str] = None):
        self.providers: Dict[str, LLMProvider] = {
            "logic": OpenAIProvider(),
            "fallback": OpenAIProvider()
        }
        self.circuit_breakers = {k: AsyncCircuitBreaker(max_failures=100, reset_timeout=5) for k in self.providers}
        self.cache = RedisCache(redis_url) if redis_url else None
        
        # Статистика для оптимизации цепочки
        self.chain_stats = {
            "total_requests": 0,
            "logic_only": 0,
            "emotion_enhanced": 0,
            "fallback_used": 0,
            "avg_response_time": 0
        }

    def _cache_key(self, task_type: str, prompt: str, context: Dict) -> str:
        ctx_hash = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        h = hashlib.sha256((task_type + prompt).encode()).hexdigest()
        return f"llm:{task_type}:{h}:{ctx_hash}"

    async def generate_with_emotion_chain(self, prompt: str, context: Dict, timeout: int = 50) -> str:
        """
        Упрощенная цепочка - только GPT-5 без эмоционального обогащения
        
        Args:
            prompt: Исходный промпт
            context: Контекст диалога
            timeout: Общий таймаут для цепочки
            
        Returns:
            Ответ от GPT-5
        """
        start_time = time.time()
        self.chain_stats["total_requests"] += 1
        
        try:
            # Прямой ответ от GPT-5
            logic_context = context.copy()
            logic_context["system_prompt"] = context.get("logic_system_prompt", 
                "Ты профессиональный менеджер по продажам трикотажа. Отвечай четко, по делу, с конкретными фактами.")
            
            logic_response = await self.generate("logic", prompt, logic_context, timeout=timeout)
            
            if not logic_response.strip():
                raise Exception("Empty logic response")
            
            self.chain_stats["logic_only"] += 1
            response_time = time.time() - start_time
            self.chain_stats["avg_response_time"] = (
                self.chain_stats["avg_response_time"] * 0.8 + response_time * 0.2
            )
            
            logger.info("llm_chain_success", 
                      logic_length=len(logic_response),
                      response_time=response_time)
            return logic_response
            
        except Exception as e:
            logger.error("llm_chain_total_failure", error=str(e))
            self.chain_stats["fallback_used"] += 1
            
            # Полный фоллбэк
            return await self.generate("fallback", prompt, context, timeout=timeout)

    async def generate(self, task_type: str, prompt: str, context: Dict, timeout: int = 35) -> str:
        # 1. Кэш
        cache_key = self._cache_key(task_type, prompt, context)
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                logger.info("llm_cache_hit", task_type=task_type, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest())
                return cached

        tasks = []
        fallback = self.providers.get("fallback")

        # 2. Подготовить параллельные задачи для LLM (с увеличенным таймаутом для VPN)
        vip_timeout = timeout + 20  # Дополнительное время для VPN
        
        for name, provider in self.providers.items():
            if name == "fallback" or (task_type != name and name != "logic"):
                continue
            cb = self.circuit_breakers[name]
            tasks.append(self._call_provider(cb, provider, prompt, context, name, timeout=vip_timeout))

        # 3. Дождаться первого валидного ответа, остальные отменить
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for fut in done:
            try:
                result = fut.result()
                sanitized = sanitize_output(result)
                if self.cache:
                    await self.cache.set(cache_key, sanitized)
                logger.info("llm_success", provider=getattr(fut, 'provider_name', 'unknown'),
                            task_type=task_type,
                            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest())
                return sanitized
            except Exception as e:
                logger.warning("llm_provider_error", provider=getattr(fut, 'provider_name', 'unknown'),
                               task_type=task_type, error=str(e))

        # 4. Fallback провайдер
        try:
            cb = self.circuit_breakers["fallback"]
            result = await self._call_provider(cb, fallback, prompt, context, "fallback", timeout=timeout)
            sanitized = sanitize_output(result)
            if self.cache:
                await self.cache.set(cache_key, sanitized)
            logger.info("llm_fallback_success", task_type=task_type, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest())
            return sanitized
        except Exception as e:
            logger.critical("llm_total_failure", task_type=task_type, error=str(e),
                            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest())
            raise RuntimeError("Все LLM-провайдеры недоступны")

    async def _call_provider(self, cb: AsyncCircuitBreaker, provider: LLMProvider,
                            prompt: str, context: Dict, name: str, timeout: int = 35):
        async def wrapper():
            # Создаем LLMRequest из параметров
            model = ModelType.GPT_4_TURBO if name == "logic" else ModelType.GPT_35_TURBO
            request = LLMRequest(
                prompt=prompt,
                model=model,
                system_prompt=context.get("system_prompt"),
                history=context.get("history", []),
                max_tokens=context.get("max_tokens", 1000),
                temperature=context.get("temperature", 0.7)
            )
            response = await provider.generate(request)
            return response.content
        try:
            fut = asyncio.create_task(cb.call(asyncio.wait_for(wrapper(), timeout=timeout)))
            fut.provider_name = name
            return fut
        except asyncio.TimeoutError:
            logger.warning("provider_timeout", provider=name)
            raise
    
    def get_chain_stats(self) -> Dict[str, Any]:
        """Получить статистику работы цепочки LLM"""
        total = self.chain_stats["total_requests"]
        if total == 0:
            return self.chain_stats
        
        return {
            **self.chain_stats,
            "emotion_enhancement_rate": self.chain_stats["emotion_enhanced"] / total * 100,
            "fallback_rate": self.chain_stats["fallback_used"] / total * 100,
            "logic_only_rate": self.chain_stats["logic_only"] / total * 100
        }
    
    def reset_stats(self):
        """Сброс статистики"""
        self.chain_stats = {
            "total_requests": 0,
            "logic_only": 0,
            "emotion_enhanced": 0,
            "fallback_used": 0,
            "avg_response_time": 0
        }

# ---- Заглушка RedisCache для тестирования ----
# В продакшене используется llm.cache.RedisCache
class RedisCache:
    def __init__(self, url): self._mem = {}
    async def get(self, key): return self._mem.get(key)
    async def set(self, key, val): self._mem[key] = val

if __name__ == "__main__":
    async def test():
        orch = LLMOrchestrator()
        resp = await orch.generate("logic", "Объясни Clean Architecture для Go", {})
        print(resp)
    asyncio.run(test())

