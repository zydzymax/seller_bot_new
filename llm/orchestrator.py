"""
orchestrator.py — OpenAI-only LLM оркестратор для SoVAni AI-продавца.

- Только OpenAI API (GPT-5 → GPT-4-turbo fallback)
- Circuit breaker с автоматическим восстановлением
- Redis кэш с TTL и cost tracking
- Exponential backoff retry с таймаутами
- Санитизация от role-reset атак
- Prometheus метрики и health checks

© SoVAni 2025
"""

import os
import asyncio
import hashlib
import json
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
import yaml

import httpx
from pydantic import BaseModel, Field
import redis.asyncio as redis

from utils.logging import get_logger

logger = get_logger(__name__)


class LLMConfig(BaseModel):
    """Конфигурация LLM оркестратора"""
    default_model: str = Field(default="gpt-5")
    fallback_model: str = Field(default="gpt-4-turbo")
    timeout: int = Field(default=15)
    max_retries: int = Field(default=3)
    backoff_base: float = Field(default=0.5)
    backoff_max: float = Field(default=2.0)
    cache_ttl: int = Field(default=3600)
    
    
class CircuitBreakerConfig(BaseModel):
    """Конфигурация Circuit Breaker"""
    failure_threshold: int = Field(default=5)
    timeout: int = Field(default=60)
    recovery_timeout: int = Field(default=120)
    
    
class CostTrackingConfig(BaseModel):
    """Конфигурация отслеживания стоимости"""
    enabled: bool = Field(default=True)
    currency: str = Field(default="RUB")
    gpt5_rub_per_1k_tokens: float = Field(default=0.8)
    gpt4_turbo_rub_per_1k_tokens: float = Field(default=0.6)
    
    
class LLMResponse(BaseModel):
    """Ответ от LLM"""
    content: str
    model_used: str
    tokens_used: int = 0
    cost_rub: float = 0.0
    cached: bool = False
    response_time_ms: int = 0
    
    
class CircuitBreaker:
    """Circuit Breaker для LLM запросов"""
    
    def __init__(self, config: CircuitBreakerConfig, redis_client: redis.Redis):
        self.config = config
        self.redis_client = redis_client
        
    async def can_execute(self) -> bool:
        """Проверка возможности выполнения запроса"""
        failure_count = await self.redis_client.get("llm:circuit_breaker:failures") or "0"
        last_failure = await self.redis_client.get("llm:circuit_breaker:last_failure")
        
        if int(failure_count) < self.config.failure_threshold:
            return True
            
        if last_failure:
            time_since_failure = time.time() - float(last_failure)
            if time_since_failure > self.config.timeout:
                # Попытка восстановления
                await self.redis_client.delete("llm:circuit_breaker:failures")
                await self.redis_client.delete("llm:circuit_breaker:last_failure")
                return True
                
        return False
        
    async def record_success(self):
        """Записать успешное выполнение"""
        await self.redis_client.delete("llm:circuit_breaker:failures")
        await self.redis_client.delete("llm:circuit_breaker:last_failure")
        
    async def record_failure(self):
        """Записать неуспешное выполнение"""
        current_failures = await self.redis_client.get("llm:circuit_breaker:failures") or "0"
        await self.redis_client.set("llm:circuit_breaker:failures", int(current_failures) + 1)
        await self.redis_client.set("llm:circuit_breaker:last_failure", time.time())
        
    async def get_fallback_response(self, prompt: str) -> str:
        """Получить fallback ответ при разомкнутом автомате"""
        # Попытка получить последний кэшированный ответ
        cache_key = f"llm:last_response:{hashlib.md5(prompt.encode()).hexdigest()}"
        cached = await self.redis_client.get(cache_key)
        
        if cached:
            return cached.decode('utf-8')
            
        return ("Извините, в данный момент у меня технические сложности. "
                "Пожалуйста, оставьте свой контакт, и наш менеджер свяжется с вами в ближайшее время.")


class LLMOrchestrator:
    """Оркестратор для работы с OpenAI LLM"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        # Загрузка конфигурации
        self.config = self._load_config()
        
        # Инициализация Redis
        self.redis_client = redis.from_url(redis_url)
        
        # Circuit Breaker
        self.circuit_breaker = CircuitBreaker(
            self.config.get('circuit_breaker', CircuitBreakerConfig()),
            self.redis_client
        )
        
        # HTTP клиент для OpenAI
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config['openai'].timeout),
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json"
            }
        )
        
        self.system_prompt_path = Path(__file__).parent.parent / "config" / "system_prompt.md"
        self.business_rules_path = Path(__file__).parent.parent / "config" / "business_rules.yaml"
        
    def _load_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации из файла"""
        config_path = Path(__file__).parent.parent / "config" / "llm.yaml"
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
            return {
                'openai': LLMConfig(**config.get('openai', {})),
                'circuit_breaker': CircuitBreakerConfig(**config.get('circuit_breaker', {})),
                'cost_tracking': CostTrackingConfig(**config.get('cost_tracking', {}))
            }
        except Exception as e:
            logger.warning(f"Ошибка загрузки конфигурации: {e}. Использую дефолтные значения.")
            return {
                'openai': LLMConfig(),
                'circuit_breaker': CircuitBreakerConfig(),
                'cost_tracking': CostTrackingConfig()
            }
            
    def _load_system_prompt(self) -> str:
        """Загрузка системного промпта"""
        try:
            with open(self.system_prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Ошибка загрузки системного промпта: {e}")
            return "Ты — AI-помощник фабрики SoVAni. Помогай клиентам с заказами текстиля."
            
    def _load_business_rules(self) -> Dict[str, Any]:
        """Загрузка бизнес-правил"""
        try:
            with open(self.business_rules_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки бизнес-правил: {e}")
            return {}
            
    def _sanitize_prompt(self, prompt: str, business_rules: Dict[str, Any]) -> str:
        """Санитизация промпта от role-reset атак"""
        guard_patterns = business_rules.get('guards', {}).get('role_reset_patterns', [])
        
        sanitized = prompt.lower()
        for pattern in guard_patterns:
            if pattern.lower() in sanitized:
                logger.warning(f"Обнаружена попытка role-reset: {pattern}")
                return "Пожалуйста, переформулируйте ваш запрос более корректно."
                
        return prompt
        
    def _generate_cache_key(self, system_prompt: str, user_prompt: str, context: Dict[str, Any]) -> str:
        """Генерация ключа кэша"""
        content = f"{system_prompt}|{user_prompt}|{json.dumps(context, sort_keys=True)}"
        return f"llm:cache:{hashlib.md5(content.encode()).hexdigest()}"
        
    def _estimate_tokens(self, text: str) -> int:
        """Примерная оценка количества токенов"""
        # Примерная формула: 1 токен ~= 4 символа для английского, 2-3 для русского
        return max(1, len(text) // 3)
        
    def _calculate_cost(self, tokens: int, model: str, config: CostTrackingConfig) -> float:
        """Расчёт стоимости запроса"""
        if not config.enabled:
            return 0.0
            
        if "gpt-5" in model:
            return (tokens / 1000) * config.gpt5_rub_per_1k_tokens
        elif "gpt-4" in model:
            return (tokens / 1000) * config.gpt4_turbo_rub_per_1k_tokens
        else:
            return (tokens / 1000) * config.gpt4_turbo_rub_per_1k_tokens
            
    async def _make_openai_request(self, messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
        """Выполнение запроса к OpenAI API"""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2048
        }
        
        response = await self.http_client.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload
        )
        
        if response.status_code != 200:
            response_text = await response.aread()
            raise Exception(f"OpenAI API error {response.status_code}: {response_text}")
            
        return response.json()
        
    async def _execute_with_retry(self, messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
        """Выполнение запроса с повторными попытками"""
        config = self.config['openai']
        last_exception = None
        
        for attempt in range(config.max_retries):
            try:
                return await self._make_openai_request(messages, model)
            except Exception as e:
                last_exception = e
                
                if attempt < config.max_retries - 1:
                    # Exponential backoff
                    delay = min(
                        config.backoff_base * (2 ** attempt),
                        config.backoff_max
                    )
                    logger.warning(f"Попытка {attempt + 1} неуспешна: {e}. Повтор через {delay}s")
                    await asyncio.sleep(delay)
                    
        raise last_exception
        
    async def generate_response(
        self, 
        user_prompt: str, 
        context: Optional[Dict[str, Any]] = None,
        force_model: Optional[str] = None
    ) -> LLMResponse:
        """Генерация ответа от LLM"""
        start_time = time.time()
        context = context or {}
        
        try:
            # Проверка Circuit Breaker
            if not await self.circuit_breaker.can_execute():
                logger.warning("Circuit Breaker разомкнут, используем fallback")
                fallback_content = await self.circuit_breaker.get_fallback_response(user_prompt)
                return LLMResponse(
                    content=fallback_content,
                    model_used="fallback",
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
                
            # Загрузка конфигурации и промптов
            system_prompt = self._load_system_prompt()
            business_rules = self._load_business_rules()
            
            # Санитизация входного промпта
            sanitized_prompt = self._sanitize_prompt(user_prompt, business_rules)
            if sanitized_prompt != user_prompt:
                return LLMResponse(
                    content=sanitized_prompt,
                    model_used="guard",
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
                
            # Проверка кэша
            cache_key = self._generate_cache_key(system_prompt, user_prompt, context)
            cached_response = await self.redis_client.get(cache_key)
            
            if cached_response:
                logger.info("Ответ получен из кэша")
                cached_data = json.loads(cached_response.decode('utf-8'))
                return LLMResponse(
                    **cached_data,
                    cached=True,
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
                
            # Подготовка сообщений для API
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            # Добавление контекста если есть
            if context:
                context_str = f"Контекст диалога: {json.dumps(context, ensure_ascii=False, indent=2)}"
                messages.insert(1, {"role": "system", "content": context_str})
                
            # Выбор модели
            model = force_model or os.getenv('LLM_MODEL', self.config['openai'].default_model)
            
            try:
                # Попытка с основной моделью
                response_data = await self._execute_with_retry(messages, model)
            except Exception as e:
                logger.warning(f"Ошибка с моделью {model}: {e}. Попытка fallback на {self.config['openai'].fallback_model}")
                
                # Fallback на другую модель
                model = self.config['openai'].fallback_model
                response_data = await self._execute_with_retry(messages, model)
                
            # Извлечение ответа
            content = response_data['choices'][0]['message']['content']
            tokens_used = response_data.get('usage', {}).get('total_tokens', 0)
            
            # Если токены не указаны, оцениваем
            if not tokens_used:
                tokens_used = self._estimate_tokens(system_prompt + user_prompt + content)
                
            # Расчёт стоимости
            cost = self._calculate_cost(tokens_used, model, self.config['cost_tracking'])
            
            # Создание ответа
            llm_response = LLMResponse(
                content=content,
                model_used=model,
                tokens_used=tokens_used,
                cost_rub=cost,
                cached=False,
                response_time_ms=int((time.time() - start_time) * 1000)
            )
            
            # Сохранение в кэш
            await self.redis_client.setex(
                cache_key,
                self.config['openai'].cache_ttl,
                json.dumps(llm_response.dict(exclude={'cached', 'response_time_ms'}))
            )
            
            # Сохранение последнего ответа для fallback
            last_response_key = f"llm:last_response:{hashlib.md5(user_prompt.encode()).hexdigest()}"
            await self.redis_client.setex(last_response_key, 86400, content)
            
            # Обновление метрик стоимости
            if self.config['cost_tracking'].enabled:
                await self._update_cost_metrics(tokens_used, cost)
                
            # Успешное выполнение
            await self.circuit_breaker.record_success()
            
            logger.info(f"LLM ответ сгенерирован: {len(content)} символов, {tokens_used} токенов, {cost:.2f} руб.")
            return llm_response
            
        except Exception as e:
            # Регистрация неудачи в Circuit Breaker
            await self.circuit_breaker.record_failure()
            
            logger.error(f"Ошибка генерации ответа LLM: {e}")
            
            # Возврат fallback ответа
            fallback_content = await self.circuit_breaker.get_fallback_response(user_prompt)
            return LLMResponse(
                content=fallback_content,
                model_used="error_fallback",
                response_time_ms=int((time.time() - start_time) * 1000)
            )
            
    async def _update_cost_metrics(self, tokens: int, cost: float):
        """Обновление метрик стоимости в Redis"""
        try:
            # Обновление общих счётчиков
            await self.redis_client.incrbyfloat("llm:metrics:total_cost_rub", cost)
            await self.redis_client.incrby("llm:metrics:total_tokens", tokens)
            
            # Метрики по дням
            today = time.strftime("%Y-%m-%d")
            await self.redis_client.incrbyfloat(f"llm:metrics:daily_cost:{today}", cost)
            await self.redis_client.incrby(f"llm:metrics:daily_tokens:{today}", tokens)
            
            # TTL на дневные метрики (30 дней)
            await self.redis_client.expire(f"llm:metrics:daily_cost:{today}", 2592000)
            await self.redis_client.expire(f"llm:metrics:daily_tokens:{today}", 2592000)
            
        except Exception as e:
            logger.warning(f"Ошибка обновления метрик стоимости: {e}")
            
    async def get_cost_metrics(self) -> Dict[str, Any]:
        """Получение метрик стоимости"""
        try:
            total_cost = await self.redis_client.get("llm:metrics:total_cost_rub") or "0"
            total_tokens = await self.redis_client.get("llm:metrics:total_tokens") or "0"
            
            today = time.strftime("%Y-%m-%d")
            daily_cost = await self.redis_client.get(f"llm:metrics:daily_cost:{today}") or "0"
            daily_tokens = await self.redis_client.get(f"llm:metrics:daily_tokens:{today}") or "0"
            
            return {
                "total_cost_rub": float(total_cost),
                "total_tokens": int(total_tokens),
                "daily_cost_rub": float(daily_cost),
                "daily_tokens": int(daily_tokens),
                "date": today
            }
        except Exception as e:
            logger.error(f"Ошибка получения метрик стоимости: {e}")
            return {}
            
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния оркестратора"""
        health = {
            "status": "healthy",
            "openai_api_key_configured": bool(os.getenv('OPENAI_API_KEY')),
            "redis_connected": False,
            "circuit_breaker_status": "closed",
            "models": {
                "default": self.config['openai'].default_model,
                "fallback": self.config['openai'].fallback_model
            }
        }
        
        try:
            # Проверка Redis
            await self.redis_client.ping()
            health["redis_connected"] = True
        except Exception as e:
            health["status"] = "degraded"
            health["redis_error"] = str(e)
            
        try:
            # Проверка Circuit Breaker
            if not await self.circuit_breaker.can_execute():
                health["circuit_breaker_status"] = "open"
                health["status"] = "degraded"
        except Exception as e:
            health["circuit_breaker_error"] = str(e)
            
        return health
        
    async def close(self):
        """Закрытие соединений"""
        try:
            await self.redis_client.close()
            await self.http_client.aclose()
        except Exception as e:
            logger.error(f"Ошибка закрытия соединений: {e}")


# Singleton instance
_orchestrator_instance = None


async def get_orchestrator(redis_url: str = None) -> LLMOrchestrator:
    """Получение singleton экземпляра оркестратора"""
    global _orchestrator_instance
    
    if _orchestrator_instance is None:
        redis_url = redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0')
        _orchestrator_instance = LLMOrchestrator(redis_url)
        
    return _orchestrator_instance