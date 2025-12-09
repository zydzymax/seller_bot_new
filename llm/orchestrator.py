"""
orchestrator.py — Универсальный LLM оркестратор для SoVAni AI-продавца.

- OpenAI Responses API + Chat Completions fallback
- Anthropic Claude поддержка
- Автоматическое определение доступных моделей
- Валидируемая карта моделей с безопасным fallback
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
import orjson

from utils.logging import get_logger

logger = get_logger(__name__)

# Валидируемая карта моделей: OpenAI-only
MODEL_MAP = {
    "gpt-5": ("openai", "gpt-5"),
    "gpt-5.1": ("openai", "gpt-5.1"),  # старшая модель для сложных случаев
    "gpt-5-mini": ("openai", "gpt-5-mini"),
    "gpt-5-nano": ("openai", "gpt-5-nano"),
    "gpt-4o": ("openai", "gpt-4o"),
    "gpt-4o-mini": ("openai", "gpt-4o-mini"),
    "gpt-4.1-mini": ("openai", "gpt-4.1-mini"),  # базовая модель
    "gpt-4-turbo": ("openai", "gpt-4-turbo"),
    "gpt4t": ("openai", "gpt-4-turbo")  # алиас
}


def resolve_model(name: str) -> tuple[str, str]:
    """
    Разрешить алиас модели в (провайдер, ID).
    
    Args:
        name: Имя модели или алиас
        
    Returns:
        Tuple (provider, model_id)
        
    Raises:
        ValueError: При неизвестной модели
    """
    if name not in MODEL_MAP:
        available = list(MODEL_MAP.keys())
        raise ValueError(f"Неизвестная модель '{name}'. Доступны: {available}")
    return MODEL_MAP[name]


class LLMConfig(BaseModel):
    """Конфигурация LLM оркестратора"""
    default_model: str = Field(default="gpt-4o")
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


class BudgetConfig(BaseModel):
    """Конфигурация бюджетных лимитов"""
    per_request_tokens: int = Field(default=6000)
    per_minute_tokens: int = Field(default=60000)
    monthly_tokens: int = Field(default=50_000_000)
    action_on_exceed: str = Field(default="truncate")  # truncate|summarize|reject


class TruncateStrategyConfig(BaseModel):
    """Конфигурация стратегии обрезки контекста"""
    prefer: str = Field(default="from_context_start")
    summarize_min_tokens: int = Field(default=800)
    
    
class LLMResponse(BaseModel):
    """Ответ от LLM"""
    content: str
    model_used: str
    tokens_used: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_rub: float = 0.0
    cached: bool = False
    response_time_ms: int = 0
    fallback_reason: Optional[str] = None
    budget_state: Dict[str, Any] = {}
    api_used: str = "chat_completions"  # responses_api | chat_completions
    
    
class RateLimiter:
    """Token Bucket Rate Limiter для LLM запросов"""
    
    def __init__(self, redis_client: redis.Redis, requests_per_second: float = 2.0, burst_size: int = 5):
        self.redis_client = redis_client
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        
    async def is_allowed(self, key: str) -> bool:
        """Проверка разрешения запроса с помощью token bucket алгоритма"""
        now = time.time()
        bucket_key = f"rate_limit:{key}"
        
        try:
            # Получение текущего состояния bucket
            bucket_data = await self.redis_client.hgetall(bucket_key)
            
            if not bucket_data:
                # Новый bucket - инициализация
                tokens = self.burst_size - 1  # Используем 1 токен
                last_refill = now
                await self.redis_client.hset(bucket_key, mapping={
                    "tokens": str(tokens),
                    "last_refill": str(last_refill)
                })
                await self.redis_client.expire(bucket_key, 3600)  # TTL 1 час
                return True
            
            tokens = float(bucket_data[b'tokens'])
            last_refill = float(bucket_data[b'last_refill'])
            
            # Добавление токенов на основе времени
            time_elapsed = now - last_refill
            tokens_to_add = time_elapsed * self.requests_per_second
            tokens = min(self.burst_size, tokens + tokens_to_add)
            
            if tokens >= 1.0:
                # Разрешаем запрос
                tokens -= 1.0
                await self.redis_client.hset(bucket_key, mapping={
                    "tokens": str(tokens),
                    "last_refill": str(now)
                })
                return True
            else:
                # Отклоняем запрос
                return False
                
        except Exception as e:
            logger.warning(f"Rate limiter error: {e}. Allowing request.")
            return True  # Fail-open подход
            
    async def get_retry_after(self, key: str) -> float:
        """Получение времени до следующего доступного токена"""
        try:
            bucket_key = f"rate_limit:{key}"
            bucket_data = await self.redis_client.hgetall(bucket_key)
            
            if not bucket_data:
                return 0.0
                
            tokens = float(bucket_data[b'tokens'])
            
            if tokens >= 1.0:
                return 0.0
            else:
                # Время до следующего токена
                return (1.0 - tokens) / self.requests_per_second
                
        except Exception:
            return 0.0


class CircuitBreaker:
    """Circuit Breaker для LLM запросов с автоматическим восстановлением"""
    
    def __init__(self, config: CircuitBreakerConfig, redis_client: redis.Redis, model: str):
        self.config = config
        self.redis_client = redis_client
        self.model = model
        self._state_key = f"circuit_breaker:{model}"
        
    async def get_state(self) -> str:
        """Получение состояния circuit breaker: closed/open/half_open"""
        try:
            data = await self.redis_client.hgetall(self._state_key)
            if not data:
                return "closed"
                
            failures = int(data.get(b'failures', b'0'))
            last_failure_time = float(data.get(b'last_failure', b'0'))
            
            # Если превышен порог - проверяем timeout
            if failures >= self.config.failure_threshold:
                if time.time() - last_failure_time > self.config.timeout:
                    # Переходим в half_open для тестирования
                    return "half_open"
                return "open"
            return "closed"
            
        except Exception as e:
            logger.warning(f"Circuit breaker state check error: {e}")
            return "closed"  # Fail-safe
            
    async def can_execute(self) -> bool:
        """Проверка возможности выполнения запроса"""
        state = await self.get_state()
        return state in ["closed", "half_open"]
        
    async def record_success(self):
        """Записать успешное выполнение (сброс счетчика)"""
        try:
            await self.redis_client.delete(self._state_key)
            logger.debug(f"Circuit breaker reset for {self.model}")
        except Exception as e:
            logger.warning(f"Circuit breaker success record error: {e}")
        
    async def record_failure(self):
        """Записать неуспешное выполнение"""
        try:
            current_data = await self.redis_client.hgetall(self._state_key)
            failures = int(current_data.get(b'failures', b'0')) + 1
            
            await self.redis_client.hset(self._state_key, mapping={
                "failures": str(failures),
                "last_failure": str(time.time())
            })
            await self.redis_client.expire(self._state_key, self.config.recovery_timeout)
            
            if failures >= self.config.failure_threshold:
                logger.warning(f"Circuit breaker opened for {self.model} after {failures} failures")
                
        except Exception as e:
            logger.warning(f"Circuit breaker failure record error: {e}")
        
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
    """Универсальный оркестратор для работы с LLM"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        # Загрузка конфигурации
        self.config = self._load_config()
        
        # Инициализация Redis
        self.redis_client = redis.from_url(redis_url)
        
        # Rate Limiter
        self.rate_limiter = RateLimiter(
            self.redis_client,
            requests_per_second=2.0,
            burst_size=5
        )
        
        # Circuit Breakers по моделям (создаются по требованию)
        self._circuit_breakers = {}
        
        # HTTP клиент (OpenAI-only)
        self.openai_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.get('timeouts', {}).get('read_ms', 60000) / 1000),
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json"
            }
        )
        
        # Предпочитаем Responses API
        self.prefer_responses_api = True
        
        self.system_prompt_path = Path(__file__).parent.parent / "config" / "system_prompt.md"
        self.business_rules_path = Path(__file__).parent.parent / "config" / "business_rules.yaml"
        
        # Кэш доступных моделей
        self._available_models = None
        
    async def detect_available_models_openai(self, timeout: float = 3.0) -> List[str]:
        """
        Определить доступные модели OpenAI с кэшированием.
        
        Args:
            timeout: Таймаут запроса в секундах
            
        Returns:
            List доступных model ID
        """
        if self._available_models is not None:
            return self._available_models
            
        try:
            # Маскируем ключ в логах
            masked_key = os.getenv('OPENAI_API_KEY', '')
            key_preview = f"{masked_key[:4]}...{masked_key[-2:]}" if len(masked_key) > 6 else "****"
            
            response = await self.openai_client.get(
                "https://api.openai.com/v1/models",
                timeout=timeout
            )
            if response.status_code == 200:
                data = response.json()
                self._available_models = [model["id"] for model in data.get("data", [])]
                logger.info(f"OpenAI models detected: {len(self._available_models)} models (key: {key_preview})")
                
                # Логируем доступность GPT-5
                has_gpt5 = "gpt-5" in self._available_models
                logger.info(f"GPT-5 availability: {has_gpt5}")
            else:
                logger.warning(f"Failed to fetch OpenAI models: {response.status_code}")
                self._available_models = ["gpt-4o", "gpt-4-turbo"]  # Fallback
        except Exception as e:
            logger.warning(f"Error detecting OpenAI models: {e}. Using fallback list.")
            self._available_models = ["gpt-4o", "gpt-4-turbo"]
            
        return self._available_models
    
    def _get_circuit_breaker(self, model: str) -> CircuitBreaker:
        """Получение Circuit Breaker для модели (ленивая инициализация)"""
        if model not in self._circuit_breakers:
            self._circuit_breakers[model] = CircuitBreaker(
                self.config.get('circuit_breaker', CircuitBreakerConfig()),
                self.redis_client,
                model
            )
        return self._circuit_breakers[model]
        
    async def choose_runtime_model(self, cfg_default: str) -> str:
        """
        Выбрать runtime модель с GPT-5-first автодетектом.
        
        Args:
            cfg_default: Модель из конфигурации
            
        Returns:
            Финальная модель для использования
        """
        try:
            # Проверяем, что модель известна в нашей карте (OpenAI-only)
            provider, model_id = resolve_model(cfg_default)
            
            available_models = await self.detect_available_models_openai()
            
            if cfg_default == "gpt-5":
                if "gpt-5" in available_models:
                    logger.info(f"Model chosen: gpt-5 -> gpt-5 (source: cfg, api: will_prefer_responses)")
                    return "gpt-5"
                else:
                    fallback = "gpt-4o"
                    logger.warning(f"GPT-5 not available in account, falling back to {fallback} (source: auto)")
                    return fallback
            else:
                # Для других моделей - обычная логика
                if model_id in available_models:
                    logger.info(f"Model chosen: {cfg_default} -> {model_id} (source: cfg)")
                    return cfg_default
                else:
                    fallback = "gpt-4o"
                    logger.warning(f"Model {cfg_default} not available, falling back to {fallback}")
                    return fallback
            
        except ValueError as e:
            logger.error(f"Invalid model in config: {e}. Using gpt-4o fallback.")
            return "gpt-4o"
    
    async def _execute_with_jitter(self, func, *args, max_attempts: int = 3, base_delay: float = 0.25, max_delay: float = 2.0, **kwargs):
        """Выполнение функции с exponential backoff + jitter"""
        import random
        
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if attempt < max_attempts - 1:
                    # Exponential backoff с jitter
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = delay * 0.1 * random.random()  # ±10% jitter
                    total_delay = delay + jitter
                    
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {total_delay:.2f}s")
                    await asyncio.sleep(total_delay)
                    
        raise last_exception
        
    async def run_completion(self, provider: str, model_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Единая функция для выполнения completion через OpenAI.
        
        Args:
            provider: Провайдер (должен быть "openai")  
            model_id: ID модели OpenAI
            messages: Список сообщений
            
        Returns:
            Ответ в унифицированном формате
        """
        if provider != "openai":
            raise ValueError(f"Only OpenAI provider supported, got: {provider}")
            
        # Собираем input для Responses API из messages
        input_text = ""
        for msg in messages:
            if msg["role"] == "system":
                input_text += f"System: {msg['content']}\n"
            elif msg["role"] == "user":
                input_text += f"User: {msg['content']}\n"
        
        timeouts = self.config.get('timeouts', {})
        retries_cfg = self.config.get('retries', {})
        
        max_attempts = retries_cfg.get('max_attempts', 3)
        backoff_ms = retries_cfg.get('backoff_ms', 250)
        
        for attempt in range(max_attempts):
            try:
                if self.prefer_responses_api:
                    # Пробуем Responses API
                    try:
                        payload = {
                            "model": model_id,
                            "input": input_text.strip()
                        }
                        
                        response = await self.openai_client.post(
                            "https://api.openai.com/v1/responses",
                            json=payload,
                            timeout=timeouts.get('read_ms', 60000) / 1000
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            logger.info(f"OpenAI Responses API success: model={model_id}, api=responses")
                            # Normalize to Chat Completions format
                            # Responses API returns: {"output": [{"content": [{"text": "..."}]}]}
                            if 'choices' not in data and 'output' in data:
                                output_text = ""
                                for output_item in data.get('output', []):
                                    for content_item in output_item.get('content', []):
                                        if content_item.get('type') == 'output_text':
                                            output_text += content_item.get('text', '')
                                        elif 'text' in content_item:
                                            output_text += content_item.get('text', '')
                                # Convert to Chat Completions format
                                data = {
                                    'choices': [{'message': {'content': output_text or str(data)}}],
                                    'usage': data.get('usage', {})
                                }
                            return data
                        elif response.status_code in [404, 405, 501]:  # Method not allowed/Not implemented
                            logger.warning(f"Responses API not supported, falling back to chat/completions")
                            # Продолжаем к chat/completions ниже
                        else:
                            response_text = response.text[:200]
                            logger.warning(f"Responses API error {response.status_code}: {response_text}")
                            # Продолжаем к chat/completions ниже
                    except Exception as e:
                        logger.warning(f"Responses API exception: {e}, falling back to chat/completions")
                
                # Fallback на Chat Completions API
                payload = {
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": 2048 if not model_id.startswith("gpt-5") else None,
                    "temperature": 0.7
                }
                
                # Для GPT-5 используем max_completion_tokens
                if model_id.startswith("gpt-5"):
                    payload["max_completion_tokens"] = 2048
                
                response = await self.openai_client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    timeout=timeouts.get('read_ms', 60000) / 1000
                )
                
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"OpenAI Chat Completions success: model={model_id}, api=chat")
                    return data
                else:
                    response_text = response.text[:200]
                    raise Exception(f"OpenAI API error {response.status_code}: {response_text}")
                    
            except Exception as e:
                if attempt < max_attempts - 1:
                    delay = min(backoff_ms * (2 ** attempt) / 1000, 2.0)
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise e
        
    def _load_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации из файла"""
        config_path = Path(__file__).parent.parent / "config" / "llm.yaml"
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
            # Store raw config for dict-style access
            result = {
                'openai': LLMConfig(**config.get('openai', {})),
                'circuit_breaker': CircuitBreakerConfig(**config.get('circuit_breaker', {})),
                'cost_tracking': CostTrackingConfig(**config.get('cost_tracking', {})),
                'budgets': BudgetConfig(**config.get('budgets', {})),
                'truncate_strategy': TruncateStrategyConfig(**config.get('truncate_strategy', {})),
                # Keep raw sections for dict-style access
                'timeouts': config.get('timeouts', {'connect_ms': 5000, 'read_ms': 60000}),
                'retries': config.get('retries', {'max_attempts': 3, 'backoff_ms': 250}),
            }
            return result
        except Exception as e:
            logger.warning(f"Ошибка загрузки конфигурации: {e}. Использую дефолтные значения.")
            return {
                'openai': LLMConfig(),
                'circuit_breaker': CircuitBreakerConfig(),
                'cost_tracking': CostTrackingConfig(),
                'budgets': BudgetConfig(),
                'truncate_strategy': TruncateStrategyConfig(),
                'timeouts': {'connect_ms': 5000, 'read_ms': 60000},
                'retries': {'max_attempts': 3, 'backoff_ms': 250},
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
        
    async def _check_budget_limits(self, estimated_tokens: int) -> Dict[str, Any]:
        """Проверка бюджетных лимитов"""
        budget_config = self.config['budgets']
        
        # Per-request limit
        if estimated_tokens > budget_config.per_request_tokens:
            return {
                "exceeded": True,
                "limit_type": "per_request", 
                "limit": budget_config.per_request_tokens,
                "estimated": estimated_tokens,
                "action": budget_config.action_on_exceed
            }
        
        # Per-minute limit
        now = time.time()
        minute_key = f"llm:minute:{time.strftime('%Y%m%d%H%M', time.gmtime(now))}"
        minute_used = int(await self.redis_client.get(minute_key) or 0)
        
        if minute_used + estimated_tokens > budget_config.per_minute_tokens:
            return {
                "exceeded": True,
                "limit_type": "per_minute",
                "limit": budget_config.per_minute_tokens,
                "used": minute_used,
                "estimated": estimated_tokens,
                "action": budget_config.action_on_exceed
            }
        
        # Monthly limit
        month_key = f"llm:monthly:{time.strftime('%Y%m', time.gmtime(now))}"
        monthly_used = int(await self.redis_client.get(month_key) or 0)
        
        if monthly_used + estimated_tokens > budget_config.monthly_tokens:
            return {
                "exceeded": True,
                "limit_type": "monthly",
                "limit": budget_config.monthly_tokens,
                "used": monthly_used,
                "estimated": estimated_tokens,
                "action": "aggressive_truncate"  # Жесткая обрезка для месячного лимита
            }
        
        return {
            "exceeded": False,
            "minute_used": minute_used,
            "monthly_used": monthly_used,
            "estimated": estimated_tokens
        }
        
    async def _update_budget_usage(self, tokens_used: int):
        """Обновление использования бюджета"""
        now = time.time()
        
        # Per-minute counter
        minute_key = f"llm:minute:{time.strftime('%Y%m%d%H%M', time.gmtime(now))}"
        await self.redis_client.incrby(minute_key, tokens_used)
        await self.redis_client.expire(minute_key, 120)  # 2 минуты TTL
        
        # Monthly counter
        month_key = f"llm:monthly:{time.strftime('%Y%m', time.gmtime(now))}"
        await self.redis_client.incrby(month_key, tokens_used)
        await self.redis_client.expire(month_key, 32 * 24 * 3600)  # 32 дня TTL
        
    async def _get_budget_state(self) -> Dict[str, Any]:
        """Получение текущего состояния бюджета"""
        now = time.time()
        
        minute_key = f"llm:minute:{time.strftime('%Y%m%d%H%M', time.gmtime(now))}"
        month_key = f"llm:monthly:{time.strftime('%Y%m', time.gmtime(now))}"
        
        minute_used = int(await self.redis_client.get(minute_key) or 0)
        monthly_used = int(await self.redis_client.get(month_key) or 0)
        
        budget_config = self.config['budgets']
        
        return {
            "minute_used": minute_used,
            "minute_limit": budget_config.per_minute_tokens,
            "minute_remaining": max(0, budget_config.per_minute_tokens - minute_used),
            "monthly_used": monthly_used,
            "monthly_limit": budget_config.monthly_tokens,
            "monthly_remaining": max(0, budget_config.monthly_tokens - monthly_used),
            "per_request_limit": budget_config.per_request_tokens
        }
        
    def _truncate_context(self, messages: List[Dict[str, str]], target_tokens: int) -> List[Dict[str, str]]:
        """Обрезка контекста по стратегии"""
        strategy = self.config['truncate_strategy']
        
        if strategy.prefer == "from_context_start":
            # Сохраняем system prompt и последнее user сообщение
            if len(messages) <= 2:
                return messages
                
            system_msg = messages[0] if messages[0]["role"] == "system" else None
            last_user_msg = None
            
            # Находим последнее пользовательское сообщение
            for msg in reversed(messages):
                if msg["role"] == "user":
                    last_user_msg = msg
                    break
            
            result = []
            if system_msg:
                result.append(system_msg)
            if last_user_msg:
                result.append(last_user_msg)
                
            # Добавляем сводку удаленного контекста
            if len(messages) > len(result):
                removed_count = len(messages) - len(result)
                summary_msg = {
                    "role": "system", 
                    "content": f"[Контекст сокращен: удалено {removed_count} сообщений для соблюдения лимитов]"
                }
                result.insert(-1, summary_msg)  # Вставляем перед последним user сообщением
                
            return result
        
        return messages  # Fallback - возвращаем без изменений
        
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
            "messages": messages
        }
        
        # Для gpt-5 используем max_completion_tokens и не устанавливаем temperature
        if "gpt-5" in model:
            payload["max_completion_tokens"] = 2048
            # temperature не поддерживается для gpt-5, используется значение по умолчанию (1)
        else:
            payload["max_tokens"] = 2048
            payload["temperature"] = 0.7
        
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
        
    # Claude/Anthropic support removed - OpenAI-only configuration
        
    def _generate_idempotency_key(self, user_prompt: str, context: Dict[str, Any], model: str) -> str:
        """Генерация idempotency key для запроса"""
        content = {
            "prompt": user_prompt,
            "context": context,
            "model": model,
            "timestamp": int(time.time() / 60)  # Округляем до минуты
        }
        
        # Используем orjson для детерминированной сериализации
        serialized = orjson.dumps(content, option=orjson.OPT_SORT_KEYS)
        return f"idempotency:{hashlib.md5(serialized).hexdigest()}"
        
    async def _check_idempotency(self, key: str) -> Optional[LLMResponse]:
        """Проверка idempotency - возврат кэшированного ответа если есть"""
        try:
            cached = await self.redis_client.get(key)
            if cached:
                data = orjson.loads(cached)
                logger.info("Idempotent request detected, returning cached response")
                return LLMResponse(**data, cached=True)
        except Exception as e:
            logger.warning(f"Idempotency check error: {e}")
        return None
        
    async def _store_idempotency(self, key: str, response: LLMResponse, ttl: int = 300):
        """Сохранение ответа для idempotency (TTL 5 минут)"""
        try:
            data = response.dict(exclude={'cached', 'response_time_ms'})
            serialized = orjson.dumps(data)
            await self.redis_client.setex(key, ttl, serialized)
        except Exception as e:
            logger.warning(f"Idempotency store error: {e}")
            
    async def generate_response(
        self,
        user_prompt: str,
        context: Optional[Dict[str, Any]] = None,
        force_model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        message_history: Optional[List[Dict[str, str]]] = None
    ) -> LLMResponse:
        """Генерация ответа от LLM с новой логикой моделей"""
        start_time = time.time()
        context = context or {}
        
        try:
            # Выбор модели с автодетектом
            # Access LLMConfig attribute directly (it's a Pydantic model, not dict)
            openai_config = self.config.get('openai', LLMConfig())
            cfg_default = force_model or (openai_config.default_model if hasattr(openai_config, 'default_model') else 'gpt-4o')
            chosen_model = await self.choose_runtime_model(cfg_default)
            
            # Проверка idempotency
            idempotency_key = self._generate_idempotency_key(user_prompt, context, chosen_model)
            cached_response = await self._check_idempotency(idempotency_key)
            if cached_response:
                return cached_response
            
            # Rate limiting
            rate_limit_key = f"llm_requests:{int(time.time() / 60)}"  # По минутам
            if not await self.rate_limiter.is_allowed(rate_limit_key):
                retry_after = await self.rate_limiter.get_retry_after(rate_limit_key)
                logger.warning(f"Rate limit exceeded, retry after {retry_after:.1f}s")
                return LLMResponse(
                    content="Слишком много запросов. Пожалуйста, попробуйте через несколько секунд.",
                    model_used="rate_limited",
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
            
            # Проверка Circuit Breaker для выбранной модели
            circuit_breaker = self._get_circuit_breaker(chosen_model)
            if not await circuit_breaker.can_execute():
                logger.warning(f"Circuit Breaker открыт для {chosen_model}, используем fallback")
                fallback_content = await circuit_breaker.get_fallback_response(user_prompt)
                return LLMResponse(
                    content=fallback_content,
                    model_used="circuit_breaker_fallback",
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
                
            # Загрузка конфигурации и промптов
            # Используем переданный system_prompt если есть, иначе грузим из файла
            if not system_prompt:
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
                
            # Модель уже выбрана выше
            
            # Проверка кэша
            cache_key = self._generate_cache_key(system_prompt, user_prompt, context)
            cached_response = await self.redis_client.get(cache_key)
            
            if cached_response:
                logger.info("Ответ получен из кэша")
                try:
                    cached_data = orjson.loads(cached_response)
                except:
                    # Fallback для старого формата JSON
                    cached_data = json.loads(cached_response.decode('utf-8'))
                return LLMResponse(
                    **cached_data,
                    cached=True,
                    response_time_ms=int((time.time() - start_time) * 1000)
                )
                
            # Подготовка сообщений для API
            messages = [{"role": "system", "content": system_prompt}]

            # Добавление контекста если есть
            if context:
                context_str = f"Контекст диалога: {json.dumps(context, ensure_ascii=False)}"
                messages.append({"role": "system", "content": context_str})

            # Добавление истории сообщений (если есть)
            if message_history:
                for msg in message_history[-10:]:  # Последние 10 сообщений
                    messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

            # Добавление текущего сообщения пользователя
            messages.append({"role": "user", "content": user_prompt})
            
            # Оценка токенов для бюджетного контроля
            total_text = " ".join(msg["content"] for msg in messages)
            estimated_tokens = self._estimate_tokens(total_text)
            
            # Проверка бюджетных лимитов
            budget_check = await self._check_budget_limits(estimated_tokens)
            fallback_reason = None
            
            if budget_check["exceeded"]:
                logger.warning(f"Budget limit exceeded: {budget_check['limit_type']}", **budget_check)
                
                if budget_check["action"] == "truncate" or budget_check["action"] == "aggressive_truncate":
                    # Обрезаем контекст
                    target_tokens = budget_check.get("limit", 4000) * 0.7  # 70% от лимита
                    messages = self._truncate_context(messages, int(target_tokens))
                    fallback_reason = f"context_truncated_{budget_check['limit_type']}"
                    logger.info(f"Context truncated due to {budget_check['limit_type']} limit")
                elif budget_check["action"] == "reject":
                    return LLMResponse(
                        content="Извините, временно превышен лимит запросов. Попробуйте позже.",
                        model_used="budget_limited",
                        response_time_ms=int((time.time() - start_time) * 1000),
                        fallback_reason=f"budget_exceeded_{budget_check['limit_type']}",
                        budget_state=budget_check
                    )
            
            try:
                # Выполнение запроса с retry + jitter
                provider, model_id = resolve_model(chosen_model)
                response_data = await self._execute_with_jitter(
                    self.run_completion,
                    provider, model_id, messages,
                    max_attempts=3
                )
                
                # Успешное выполнение - сброс circuit breaker
                await circuit_breaker.record_success()
                
            except Exception as e:
                # Регистрация ошибки в circuit breaker
                await circuit_breaker.record_failure()
                
                logger.warning(f"Ошибка с моделью {chosen_model}: {e}")
                
                # Попытка fallback на gpt-4o если основная модель не gpt-4o
                if chosen_model != "gpt-4o":
                    try:
                        logger.info(f"Fallback с {chosen_model} на gpt-4o")
                        fallback_cb = self._get_circuit_breaker("gpt-4o")
                        
                        if await fallback_cb.can_execute():
                            response_data = await self._execute_with_jitter(
                                self.run_completion,
                                "openai", "gpt-4o", messages,
                                max_attempts=2
                            )
                            chosen_model = "gpt-4o"
                            await fallback_cb.record_success()
                            logger.info("Fallback на gpt-4o успешен")
                        else:
                            raise Exception("Fallback model circuit breaker is open")
                            
                    except Exception as fallback_error:
                        logger.error(f"Fallback на gpt-4o неуспешен: {fallback_error}")
                        
                        # Финальный fallback - статичный ответ
                        fallback_content = await circuit_breaker.get_fallback_response(user_prompt)
                        return LLMResponse(
                            content=fallback_content,
                            model_used="static_fallback",
                            response_time_ms=int((time.time() - start_time) * 1000)
                        )
                else:
                    # gpt-4o тоже не работает - используем статичный ответ
                    fallback_content = await circuit_breaker.get_fallback_response(user_prompt)
                    return LLMResponse(
                        content=fallback_content,
                        model_used="static_fallback",
                        response_time_ms=int((time.time() - start_time) * 1000)
                    )
                
            # Извлечение ответа и детализированной статистики
            content = response_data['choices'][0]['message']['content']
            usage = response_data.get('usage', {})
            tokens_used = usage.get('total_tokens', 0)
            tokens_input = usage.get('prompt_tokens', 0)
            tokens_output = usage.get('completion_tokens', 0)
            
            # Если токены не указаны, оцениваем
            if not tokens_used:
                estimated_input = self._estimate_tokens(" ".join(msg["content"] for msg in messages))
                estimated_output = self._estimate_tokens(content)
                tokens_input = estimated_input
                tokens_output = estimated_output
                tokens_used = estimated_input + estimated_output
            
            # Обновление бюджета
            await self._update_budget_usage(tokens_used)
            
            # Получение текущего состояния бюджета для логирования
            current_budget_state = await self._get_budget_state()
                
            # Расчёт стоимости
            cost = self._calculate_cost(tokens_used, chosen_model, self.config['cost_tracking'])
            
            # Определение API, который использовался
            api_used = "responses_api" if self.prefer_responses_api else "chat_completions"
            
            # Создание ответа
            llm_response = LLMResponse(
                content=content,
                model_used=chosen_model,
                tokens_used=tokens_used,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_rub=cost,
                cached=False,
                response_time_ms=int((time.time() - start_time) * 1000),
                fallback_reason=fallback_reason,
                budget_state=current_budget_state,
                api_used=api_used
            )
            
            # Сохранение idempotency
            await self._store_idempotency(idempotency_key, llm_response)
            
            # Сохранение в кэш
            cache_data = llm_response.dict(exclude={'cached', 'response_time_ms'})
            await self.redis_client.setex(
                cache_key,
                self.config['openai'].cache_ttl,
                orjson.dumps(cache_data)
            )
            
            # Сохранение последнего ответа для fallback
            last_response_key = f"llm:last_response:{hashlib.md5(user_prompt.encode()).hexdigest()}"
            await self.redis_client.setex(last_response_key, 86400, content)
            
            # Обновление метрик стоимости
            if self.config['cost_tracking'].enabled:
                await self._update_cost_metrics(tokens_used, cost)
                
            # Успешное выполнение (circuit_breaker уже определен выше)
            await circuit_breaker.record_success()
            
            # Структурированное логирование LLM операции
            logger.info("LLM operation completed", 
                       request_id=idempotency_key[-8:],  # Короткий ID для логов
                       model=chosen_model,
                       api=api_used,
                       latency_ms=llm_response.response_time_ms,
                       status="success",
                       tokens_in=tokens_input,
                       tokens_out=tokens_output,
                       tokens_total=tokens_used,
                       cost_rub=cost,
                       fallback_reason=fallback_reason,
                       budget_state=current_budget_state)
                       
            return llm_response
            
        except Exception as e:
            logger.error(f"Критическая ошибка генерации ответа LLM: {e}")
            
            # Возврат fallback ответа
            return LLMResponse(
                content="Извините, возникли технические сложности. Наш менеджер свяжется с вами в ближайшее время.",
                model_used="critical_error_fallback",
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
            # Проверка Circuit Breaker (используем дефолтную модель)
            default_cb = self._get_circuit_breaker('gpt-4o')
            if not await default_cb.can_execute():
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