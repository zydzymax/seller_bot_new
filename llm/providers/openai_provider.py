import os
import logging
import aiohttp
import asyncio
from typing import Dict, Any, Optional
from ..base import LLMProvider, LLMRequest, LLMResponse, ModelType, LLMError

# Тарифы
MODEL_PRICING = {
    ModelType.GPT_4_TURBO: 0.01 / 1000,
    ModelType.GPT_4: 0.03 / 1000,
    ModelType.GPT_35_TURBO: 0.001 / 1000,
}

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

logger = logging.getLogger("llm.providers.openai")
logger.setLevel(logging.INFO)

class SecretsFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, "api_key"):
            record.api_key = "***"
        return True

logger.addFilter(SecretsFilter())

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, timeout: int = 45, max_retries: int = 4, rate_limiter=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout = timeout  # Увеличен для VPN
        self.max_retries = max_retries  # Больше попыток для VPN
        self.supported_models = [ModelType.GPT_4_TURBO, ModelType.GPT_4, ModelType.GPT_35_TURBO]
        self.rate_limiter = rate_limiter  # DI-ready
        
        # Connection pooling для VPN
        self.connector = aiohttp.TCPConnector(
            limit=5,
            limit_per_host=3,
            keepalive_timeout=300,
            enable_cleanup_closed=True
        )

    @property
    def models(self):
        return self.supported_models

    def is_available(self) -> bool:
        return bool(self.api_key)

    def calculate_cost(self, usage: Dict[str, int]) -> float:
        model = usage.get("model", ModelType.GPT_4_TURBO)
        total_tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        price_per_1k = MODEL_PRICING.get(model, 0.01)
        return total_tokens * price_per_1k

    def _validate_request(self, request: LLMRequest):
        if request.model not in self.supported_models:
            raise ValueError(f"Модель {request.model} не поддерживается этим провайдером.")
        if not request.prompt or not isinstance(request.prompt, str):
            raise ValueError("Prompt пустой или некорректный.")
        if request.max_tokens < 10 or request.max_tokens > 4096:
            raise ValueError("max_tokens вне допустимого диапазона.")

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)
        if not self.api_key:
            return LLMResponse(
                content="",
                model=request.model,
                provider="openai",
                usage={},
                latency_ms=0.0,
                cached=False,
                error=LLMError(code="NO_API_KEY", message="No OpenAI API key provided"),
            )
        # Rate limiting
        if self.rate_limiter:
            await self.rate_limiter.check(request)
        # Используем GPT-5 как требовалось
        model_name = "gpt-5"
        
        payload = {
            "model": model_name,
            "messages": [],
        }
        
        # GPT-5 поддерживает только temperature=1 (по умолчанию)
        if model_name != "gpt-5":
            payload["temperature"] = request.temperature
        
        # GPT-5 требует max_completion_tokens вместо max_tokens
        # Для GPT-5 значительно увеличиваем лимит из-за reasoning токенов (особенно для русского языка)
        if model_name == "gpt-5":
            payload["max_completion_tokens"] = max(request.max_tokens * 8, 1500)
        else:
            payload["max_tokens"] = request.max_tokens
        if request.system_prompt:
            payload["messages"].append({"role": "system", "content": request.system_prompt})
        if request.history:
            payload["messages"].extend(request.history)
        payload["messages"].append({"role": "user", "content": request.prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempt = 0
        while attempt < self.max_retries:
            try:
                start = asyncio.get_event_loop().time()
                
                # Используем connection pooling для VPN
                async with aiohttp.ClientSession(
                    connector=self.connector,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as session:
                    async with session.post(OPENAI_API_URL, json=payload, headers=headers) as resp:
                        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
                        data = await resp.json()
                        
                        if resp.status != 200:
                            logger.error("openai_provider_error", status=resp.status, data=data, model=model_name, source="OpenAIProvider")
                            
                            # VPN-specific handling
                            if resp.status == 429:  # Rate limiting
                                wait_time = min(2 ** attempt, 60)  # Max 60 sec
                                logger.info(f"Rate limited, waiting {wait_time}s")
                                await asyncio.sleep(wait_time)
                                attempt += 1
                                continue
                            
                            # Retry on server errors and network issues
                            if resp.status >= 500 or resp.status in [408, 424, 502, 503, 504]:
                                wait_time = min(2 ** attempt, 30)
                                logger.info(f"Server error {resp.status}, retrying in {wait_time}s")
                                attempt += 1
                                await asyncio.sleep(wait_time)
                                continue
                            
                            # Client errors - don't retry
                            return LLMResponse(
                                content="",
                                model=request.model,
                                provider="openai",
                                usage={},
                                latency_ms=float(latency_ms),
                                cached=False,
                                error=LLMError(
                                    code=f"HTTP_{resp.status}",
                                    message=data.get("error", {}).get("message", str(data)),
                                ),
                            )
                        
                        content = data["choices"][0]["message"]["content"]
                        usage = data.get("usage", {})
                        usage["model"] = request.model
                        
                        logger.info("openai_success", latency_ms=latency_ms, attempt=attempt + 1)
                        return LLMResponse(
                            content=content,
                            model=request.model,
                            provider="openai",
                            usage=usage,
                            latency_ms=float(latency_ms),
                            cached=False,
                            error=None,
                        )
                        
            except asyncio.TimeoutError:
                wait_time = min(2 ** attempt, 30)
                logger.warning(f"OpenAI timeout, retry {attempt + 1}/{self.max_retries} in {wait_time}s")
                if attempt + 1 >= self.max_retries:
                    return LLMResponse(
                        content="",
                        model=request.model,
                        provider="openai",
                        usage={},
                        latency_ms=0.0,
                        cached=False,
                        error=LLMError(code="TIMEOUT", message="Request timeout"),
                    )
                attempt += 1
                await asyncio.sleep(wait_time)
                
            except aiohttp.ClientError as e:
                # Network errors - retry
                wait_time = min(2 ** attempt, 30)
                logger.warning(f"OpenAI network error: {str(e)}, retry {attempt + 1}/{self.max_retries}")
                if attempt + 1 >= self.max_retries:
                    return LLMResponse(
                        content="",
                        model=request.model,
                        provider="openai",
                        usage={},
                        latency_ms=0.0,
                        cached=False,
                        error=LLMError(code="NETWORK_ERROR", message=str(e)),
                    )
                attempt += 1
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                logger.exception("OpenAI API Exception (retrying)" if attempt + 1 < self.max_retries else "OpenAI API fatal error")
                if attempt + 1 >= self.max_retries:
                    return LLMResponse(
                        content="",
                        model=request.model,
                        provider="openai",
                        usage={},
                        latency_ms=0.0,
                        cached=False,
                        error=LLMError(code="API_ERROR", message=str(e)),
                    )
                attempt += 1
                await asyncio.sleep(min(2 ** attempt, 30))

