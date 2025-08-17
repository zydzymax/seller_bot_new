"""
elevenlabs_adapter.py — Оптимизированный адаптер для ElevenLabs TTS
с голосом "Алена" для SoVAni AI-продавца

Особенности:
- Асинхронные очереди и worker для быстрой обработки
- Connection pooling для VPN соединений
- Кэширование аудио в Redis
- Retry механизм с exponential backoff
- Фоллбэк на OpenAI TTS при сбоях

© SoVAni 2025
"""

import asyncio
import aiohttp
import aiofiles
import structlog
import hashlib
import time
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path
import json
from urllib.parse import urljoin

logger = structlog.get_logger("ai_seller.elevenlabs_adapter")


@dataclass
class TTSRequest:
    """Запрос на синтез речи"""
    text: str
    voice_id: str = "alena"
    model: str = "eleven_multilingual_v2"
    stability: float = 0.5
    similarity_boost: float = 0.5
    style: float = 0.0
    use_speaker_boost: bool = True
    priority: int = 5  # 1-10, где 10 = максимальный приоритет


@dataclass  
class TTSResponse:
    """Ответ синтеза речи"""
    audio_data: bytes
    duration_ms: int
    cached: bool = False
    provider: str = "elevenlabs"
    voice_used: str = "alena"


class ElevenLabsError(Exception):
    """Исключения ElevenLabs API"""
    def __init__(self, message: str, code: str = "UNKNOWN", status_code: int = 0):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class VoiceManager:
    """Управление голосами ElevenLabs"""
    
    # Голос "Алена" - молодая, дружелюбная, профессиональная
    ALENA_VOICE_CONFIG = {
        "voice_id": "pNInz6obpgDQGcFmaJgB",  # ID голоса Адам (будет заменен на кастомный)
        "name": "Алена",
        "description": "Молодая русскоговорящая девушка, 25 лет, дружелюбная и профессиональная",
        "settings": {
            "stability": 0.65,        # Стабильность (0.0-1.0)
            "similarity_boost": 0.75, # Схожесть с оригиналом
            "style": 0.2,             # Эмоциональность
            "use_speaker_boost": True  # Усиление голоса
        }
    }
    
    # Альтернативные голоса для фоллбэка
    FALLBACK_VOICES = [
        {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella"},
        {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli"},
        {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"}
    ]
    
    @classmethod
    def get_alena_config(cls) -> Dict[str, Any]:
        """Получить конфигурацию голоса Алены"""
        return cls.ALENA_VOICE_CONFIG.copy()
    
    @classmethod
    def get_fallback_voice(cls, index: int = 0) -> Dict[str, Any]:
        """Получить фоллбэк голос по индексу"""
        if 0 <= index < len(cls.FALLBACK_VOICES):
            return cls.FALLBACK_VOICES[index]
        return cls.FALLBACK_VOICES[0]


class AudioCache:
    """Кэш для аудио файлов"""
    
    def __init__(self, redis_client=None, cache_ttl: int = 3600):
        self.redis = redis_client
        self.cache_ttl = cache_ttl
        self._memory_cache = {}  # Фоллбэк кэш в памяти
        
    def _get_cache_key(self, text: str, voice_id: str, settings: Dict) -> str:
        """Генерация ключа кэша"""
        content = f"{text}:{voice_id}:{json.dumps(settings, sort_keys=True)}"
        return f"tts:elevenlabs:{hashlib.sha256(content.encode()).hexdigest()[:16]}"
    
    async def get(self, text: str, voice_id: str, settings: Dict) -> Optional[bytes]:
        """Получить аудио из кэша"""
        cache_key = self._get_cache_key(text, voice_id, settings)
        
        # Попробуем Redis
        if self.redis:
            try:
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    logger.info("audio_cache_hit_redis", cache_key=cache_key[:8])
                    return cached_data
            except Exception as e:
                logger.warning("redis_cache_error", error=str(e))
        
        # Фоллбэк на memory cache
        if cache_key in self._memory_cache:
            entry_time, audio_data = self._memory_cache[cache_key]
            if time.time() - entry_time < self.cache_ttl:
                logger.info("audio_cache_hit_memory", cache_key=cache_key[:8])
                return audio_data
            else:
                del self._memory_cache[cache_key]
        
        return None
    
    async def set(self, text: str, voice_id: str, settings: Dict, audio_data: bytes):
        """Сохранить аудио в кэш"""
        cache_key = self._get_cache_key(text, voice_id, settings)
        
        # Сохранить в Redis
        if self.redis:
            try:
                await self.redis.setex(cache_key, self.cache_ttl, audio_data)
                logger.info("audio_cached_redis", cache_key=cache_key[:8], size=len(audio_data))
            except Exception as e:
                logger.warning("redis_cache_save_error", error=str(e))
        
        # Сохранить в memory cache (лимит на размер)
        if len(audio_data) < 1024 * 1024:  # Только файлы < 1MB
            self._memory_cache[cache_key] = (time.time(), audio_data)
            
            # Очистка старых записей
            if len(self._memory_cache) > 50:
                oldest_key = min(self._memory_cache.keys(), 
                               key=lambda k: self._memory_cache[k][0])
                del self._memory_cache[oldest_key]


class ElevenLabsAdapter:
    """
    Оптимизированный адаптер для ElevenLabs TTS API
    с поддержкой VPN, очередей и кэширования
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.elevenlabs.io/v1",
        max_workers: int = 3,
        redis_client=None,
        fallback_provider=None,
        connection_timeout: int = 60,  # Увеличено для VPN
        request_timeout: int = 120,    # Увеличено для VPN
        max_retries: int = 3
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.base_url = base_url
        self.max_workers = max_workers
        self.fallback_provider = fallback_provider
        self.max_retries = max_retries
        
        if not self.api_key:
            raise ValueError("ElevenLabs API key is required")
        
        # Настройки для VPN
        self.timeout = aiohttp.ClientTimeout(
            total=request_timeout,
            connect=connection_timeout
        )
        
        # Компоненты
        self.cache = AudioCache(redis_client)
        self.voice_manager = VoiceManager()
        
        # Очередь задач
        self.request_queue = asyncio.Queue()
        self.workers = []
        self.is_running = False
        
        # Connection pool для VPN оптимизации
        self.connector = aiohttp.TCPConnector(
            limit=10,                    # Максимум соединений
            limit_per_host=5,           # На один хост
            keepalive_timeout=300,      # Держать соединение 5 мин
            enable_cleanup_closed=True
        )
        
        # Статистика
        self.stats = {
            "requests_total": 0,
            "requests_cached": 0,
            "requests_failed": 0,
            "avg_response_time_ms": 0
        }
    
    async def start_workers(self):
        """Запустить worker'ы для обработки очереди"""
        if self.is_running:
            return
        
        self.is_running = True
        self.workers = [
            asyncio.create_task(self._worker(f"worker-{i}"))
            for i in range(self.max_workers)
        ]
        logger.info("elevenlabs_workers_started", count=self.max_workers)
    
    async def stop_workers(self):
        """Остановить worker'ы"""
        self.is_running = False
        
        # Отменить задачи
        for worker in self.workers:
            worker.cancel()
        
        # Дождаться завершения
        await asyncio.gather(*self.workers, return_exceptions=True)
        
        # Закрыть connector
        await self.connector.close()
        
        logger.info("elevenlabs_workers_stopped")
    
    async def _worker(self, worker_name: str):
        """Worker для обработки TTS запросов"""
        logger.info("worker_started", name=worker_name)
        
        while self.is_running:
            try:
                # Получить задачу из очереди (с таймаутом)
                try:
                    future, request = await asyncio.wait_for(
                        self.request_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                try:
                    # Обработать запрос
                    response = await self._process_tts_request(request)
                    future.set_result(response)
                    
                except Exception as e:
                    future.set_exception(e)
                finally:
                    self.request_queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("worker_error", worker=worker_name, error=str(e))
        
        logger.info("worker_stopped", name=worker_name)
    
    async def synthesize_speech(
        self,
        text: str,
        voice: str = "alena",
        priority: int = 5,
        **kwargs
    ) -> TTSResponse:
        """
        Синтезировать речь из текста
        
        Args:
            text: Текст для синтеза
            voice: Голос ("alena" или ID голоса)
            priority: Приоритет (1-10)
            **kwargs: Дополнительные параметры
            
        Returns:
            TTSResponse с аудио данными
        """
        start_time = time.time()
        self.stats["requests_total"] += 1
        
        try:
            # Предобработка текста
            processed_text = self._preprocess_text(text)
            
            # Создать запрос
            request = TTSRequest(
                text=processed_text,
                voice_id=self._resolve_voice_id(voice),
                priority=priority,
                **kwargs
            )
            
            # Проверить кэш
            voice_config = self.voice_manager.get_alena_config()
            settings = voice_config["settings"]
            
            cached_audio = await self.cache.get(
                processed_text, request.voice_id, settings
            )
            
            if cached_audio:
                self.stats["requests_cached"] += 1
                return TTSResponse(
                    audio_data=cached_audio,
                    duration_ms=int((time.time() - start_time) * 1000),
                    cached=True,
                    voice_used=voice
                )
            
            # Поставить в очередь или обработать сразу
            if self.is_running and not self.request_queue.empty():
                future = asyncio.Future()
                await self.request_queue.put((future, request))
                response = await future
            else:
                response = await self._process_tts_request(request)
            
            # Обновить статистику
            duration_ms = int((time.time() - start_time) * 1000)
            self.stats["avg_response_time_ms"] = (
                self.stats["avg_response_time_ms"] * 0.8 + duration_ms * 0.2
            )
            
            return TTSResponse(
                audio_data=response,
                duration_ms=duration_ms,
                voice_used=voice
            )
            
        except Exception as e:
            self.stats["requests_failed"] += 1
            logger.error("synthesize_speech_error", error=str(e))
            
            # Попробовать фоллбэк
            if self.fallback_provider:
                try:
                    logger.info("trying_fallback_provider")
                    fallback_audio = await self.fallback_provider.synthesize(text, "nova")
                    return TTSResponse(
                        audio_data=fallback_audio,
                        duration_ms=int((time.time() - start_time) * 1000),
                        provider="openai_fallback",
                        voice_used="nova"
                    )
                except Exception as fallback_error:
                    logger.error("fallback_also_failed", error=str(fallback_error))
            
            raise ElevenLabsError(f"TTS synthesis failed: {str(e)}")
    
    async def _process_tts_request(self, request: TTSRequest) -> bytes:
        """Обработать TTS запрос через API"""
        voice_config = self.voice_manager.get_alena_config()
        
        payload = {
            "text": request.text,
            "model_id": request.model,
            "voice_settings": {
                "stability": request.stability,
                "similarity_boost": request.similarity_boost,
                "style": request.style,
                "use_speaker_boost": request.use_speaker_boost
            }
        }
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key
        }
        
        url = urljoin(self.base_url, f"/text-to-speech/{request.voice_id}")
        
        # Retry механизм
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession(
                    connector=self.connector,
                    timeout=self.timeout
                ) as session:
                    async with session.post(url, json=payload, headers=headers) as response:
                        
                        if response.status == 200:
                            audio_data = await response.read()
                            
                            # Сохранить в кэш
                            await self.cache.set(
                                request.text, request.voice_id, 
                                voice_config["settings"], audio_data
                            )
                            
                            logger.info("tts_success", 
                                      size=len(audio_data), 
                                      attempt=attempt + 1)
                            return audio_data
                        
                        # Обработка ошибок API
                        error_text = await response.text()
                        
                        if response.status == 429:  # Rate limit
                            wait_time = 2 ** attempt
                            logger.warning("rate_limit_hit", wait_time=wait_time)
                            await asyncio.sleep(wait_time)
                            continue
                        
                        elif response.status >= 500:  # Серверные ошибки - повторить
                            wait_time = 2 ** attempt
                            logger.warning("server_error", status=response.status, wait_time=wait_time)
                            await asyncio.sleep(wait_time)
                            continue
                        
                        else:  # Клиентские ошибки - не повторять
                            raise ElevenLabsError(
                                f"API error: {error_text}",
                                code=f"HTTP_{response.status}",
                                status_code=response.status
                            )
                            
            except asyncio.TimeoutError:
                wait_time = 2 ** attempt
                logger.warning("request_timeout", attempt=attempt + 1, wait_time=wait_time)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                raise ElevenLabsError("Request timeout after all retries")
            
            except aiohttp.ClientError as e:
                wait_time = 2 ** attempt
                logger.warning("client_error", error=str(e), attempt=attempt + 1)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                raise ElevenLabsError(f"Client error: {str(e)}")
        
        raise ElevenLabsError("All retry attempts failed")
    
    def _resolve_voice_id(self, voice: str) -> str:
        """Преобразовать имя голоса в ID"""
        if voice.lower() == "alena":
            return self.voice_manager.get_alena_config()["voice_id"]
        
        # Если передан ID напрямую
        if len(voice) > 10:  # ID обычно длинные
            return voice
        
        # Фоллбэк голоса
        fallback = self.voice_manager.get_fallback_voice(0)
        logger.warning("unknown_voice_fallback", requested=voice, used=fallback["name"])
        return fallback["voice_id"]
    
    def _preprocess_text(self, text: str) -> str:
        """Предобработка текста для лучшего синтеза"""
        if not text:
            return ""
        
        # Ограничение длины (ElevenLabs лимит ~2500 символов)
        if len(text) > 2400:
            text = text[:2400] + "..."
            logger.warning("text_truncated", original_length=len(text))
        
        # Очистка и замены для русского языка
        import re
        
        # Удаляем эмодзи
        text = re.sub(r'[^\w\s\.\,\!\?\:\;\-\(\)\"\'№\%]+', '', text)
        
        # Замены для лучшего произношения
        replacements = {
            "₽": "рублей",
            "%": "процентов", 
            "№": "номер",
            "тр-ж": "трикотаж",
            "пр-во": "производство",
            "и т.д.": "и так далее",
            "и т.п.": "и тому подобное",
            "руб.": "рублей",
            "шт.": "штук",
            "кг.": "килограмм",
            "м.": "метров"
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        # Разбивка длинных предложений
        sentences = text.split('.')
        processed_sentences = []
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 150:
                # Разбиваем по запятым
                parts = sentence.split(',')
                current = ""
                for part in parts:
                    if len(current + part) < 120:
                        current += part + ","
                    else:
                        if current:
                            processed_sentences.append(current.rstrip(','))
                        current = part + ","
                if current:
                    processed_sentences.append(current.rstrip(','))
            elif sentence:
                processed_sentences.append(sentence)
        
        result = '. '.join(processed_sentences)
        return result.strip()
    
    async def get_voices(self) -> List[Dict[str, Any]]:
        """Получить список доступных голосов"""
        headers = {"xi-api-key": self.api_key}
        url = urljoin(self.base_url, "/voices")
        
        try:
            async with aiohttp.ClientSession(
                connector=self.connector, timeout=self.timeout
            ) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("voices", [])
                    else:
                        logger.error("failed_to_get_voices", status=response.status)
                        return []
        except Exception as e:
            logger.error("get_voices_error", error=str(e))
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику использования"""
        return {
            **self.stats,
            "queue_size": self.request_queue.qsize(),
            "workers_active": len([w for w in self.workers if not w.done()]),
            "cache_type": "redis" if self.cache.redis else "memory"
        }


# Фабрика для создания адаптера
async def create_elevenlabs_adapter(
    api_key: Optional[str] = None,
    redis_client=None,
    fallback_provider=None,
    start_workers: bool = True
) -> ElevenLabsAdapter:
    """
    Создать и настроить ElevenLabsAdapter
    
    Args:
        api_key: API ключ ElevenLabs
        redis_client: Redis клиент для кэширования
        fallback_provider: Фоллбэк провайдер (например, OpenAI TTS)
        start_workers: Запустить worker'ы сразу
        
    Returns:
        Настроенный ElevenLabsAdapter
    """
    adapter = ElevenLabsAdapter(
        api_key=api_key,
        redis_client=redis_client,
        fallback_provider=fallback_provider
    )
    
    if start_workers:
        await adapter.start_workers()
    
    return adapter


if __name__ == "__main__":
    async def test_adapter():
        """Тест адаптера"""
        adapter = await create_elevenlabs_adapter(
            api_key="sk_6c469661d89b8ef2069f645f239b13c408d795e52fe1ab99"
        )
        
        try:
            response = await adapter.synthesize_speech(
                "Привет! Меня зовут Алена, и я помогу вам с заказом трикотажа."
            )
            print(f"Синтез успешен: {len(response.audio_data)} байт")
            print(f"Статистика: {adapter.get_stats()}")
        finally:
            await adapter.stop_workers()
    
    asyncio.run(test_adapter())