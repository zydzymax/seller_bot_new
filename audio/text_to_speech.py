"""
text_to_speech.py — Модуль синтеза речи для AI-продавца
Поддерживает OpenAI TTS, ElevenLabs и другие TTS провайдеры
© SoVAni 2025
"""

import os
import io
import tempfile
import structlog
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union
import aiohttp
import aiofiles
from pathlib import Path

logger = structlog.get_logger("ai_seller.text_to_speech")


class TTSProvider(ABC):
    """Абстрактный базовый класс для провайдеров синтеза речи"""
    
    @abstractmethod
    async def synthesize(self, text: str, voice: str = "default") -> bytes:
        """Синтезировать речь из текста"""
        pass
    
    @abstractmethod
    def get_available_voices(self) -> Dict[str, str]:
        """Получить список доступных голосов"""
        pass


class OpenAITTS(TTSProvider):
    """OpenAI TTS провайдер"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"
        
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
    
    def get_available_voices(self) -> Dict[str, str]:
        """Доступные голоса OpenAI TTS"""
        return {
            "alloy": "Нейтральный голос (Alloy)",
            "echo": "Мужской голос (Echo)", 
            "fable": "Британский мужской голос (Fable)",
            "onyx": "Глубокий мужской голос (Onyx)",
            "nova": "Женский голос (Nova)",
            "shimmer": "Мягкий женский голос (Shimmer)"
        }
    
    async def synthesize(self, text: str, voice: str = "nova") -> bytes:
        """
        Синтезировать речь используя OpenAI TTS API
        
        Args:
            text: Текст для синтеза
            voice: Голос (alloy, echo, fable, onyx, nova, shimmer)
            
        Returns:
            Аудио в формате MP3
        """
        try:
            # Ограничиваем длину текста
            if len(text) > 4000:
                text = text[:4000] + "..."
                logger.warning("text_truncated", original_length=len(text))
            
            payload = {
                "model": "tts-1",
                "input": text,
                "voice": voice,
                "response_format": "mp3"
            }
            
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/audio/speech",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info("tts_synthesis_success", 
                                  text_length=len(text), audio_size=len(audio_data))
                        return audio_data
                    else:
                        error_text = await response.text()
                        logger.error("tts_synthesis_error",
                                   status=response.status, error=error_text)
                        raise Exception(f"OpenAI TTS error: {response.status} {error_text}")
                        
        except Exception as e:
            logger.error("tts_synthesis_exception", error=str(e))
            raise Exception(f"Ошибка синтеза речи: {str(e)}")


class ElevenLabsTTS(TTSProvider):
    """ElevenLabs TTS провайдер"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.base_url = "https://api.elevenlabs.io/v1"
        
        if not self.api_key:
            raise ValueError("ElevenLabs API key is required")
    
    def get_available_voices(self) -> Dict[str, str]:
        """Доступные голоса ElevenLabs (примерные)"""
        return {
            "rachel": "Rachel (Женский американский)",
            "domi": "Domi (Женский американский)",
            "bella": "Bella (Женский американский)",
            "antoni": "Antoni (Мужской американский)",
            "elli": "Elli (Женский американский)",
            "josh": "Josh (Мужской американский)"
        }
    
    async def synthesize(self, text: str, voice: str = "rachel") -> bytes:
        """
        Синтезировать речь используя ElevenLabs API
        
        Args:
            text: Текст для синтеза
            voice: ID или имя голоса
            
        Returns:
            Аудио в формате MP3
        """
        try:
            # Ограничиваем длину текста
            if len(text) > 2500:
                text = text[:2500] + "..."
            
            # Маппинг имен голосов на ID (это примерные ID)
            voice_mapping = {
                "rachel": "21m00Tcm4TlvDq8ikWAM",
                "domi": "AZnzlk1XvdvUeBnXmlld", 
                "bella": "EXAVITQu4vr4xnSDxMaL",
                "antoni": "ErXwobaYiN019PkySvjV",
                "elli": "MF3mGyEYCl7XYWbV9V6O",
                "josh": "TxGEqnHWrfWFTfGW9XjX"
            }
            
            voice_id = voice_mapping.get(voice, voice)
            
            payload = {
                "text": text,
                "model_id": "eleven_multilingual_v2", 
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.5
                }
            }
            
            headers = {
                'Accept': 'audio/mpeg',
                'Content-Type': 'application/json',
                'xi-api-key': self.api_key
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/text-to-speech/{voice_id}",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info("elevenlabs_synthesis_success",
                                  text_length=len(text), audio_size=len(audio_data))
                        return audio_data
                    else:
                        error_text = await response.text()
                        logger.error("elevenlabs_synthesis_error",
                                   status=response.status, error=error_text)
                        raise Exception(f"ElevenLabs TTS error: {response.status} {error_text}")
                        
        except Exception as e:
            logger.error("elevenlabs_synthesis_exception", error=str(e))
            raise Exception(f"Ошибка синтеза речи ElevenLabs: {str(e)}")


class TextToSpeechService:
    """Сервис синтеза речи с поддержкой нескольких провайдеров"""
    
    def __init__(self, provider: TTSProvider):
        self.provider = provider
        self.temp_dir = Path(tempfile.gettempdir()) / "ai_seller_tts"
        self.temp_dir.mkdir(exist_ok=True)
    
    async def synthesize_text(self, text: str, voice: str = "default") -> bytes:
        """
        Синтезировать речь из текста
        
        Args:
            text: Текст для синтеза
            voice: Голос для использования
            
        Returns:
            Аудио данные в формате MP3
        """
        try:
            # Предварительная обработка текста
            processed_text = self._preprocess_text(text)
            
            # Синтез речи
            audio_data = await self.provider.synthesize(processed_text, voice)
            
            logger.info("text_synthesis_success", 
                       original_length=len(text), processed_length=len(processed_text))
            return audio_data
            
        except Exception as e:
            logger.error("text_synthesis_error", error=str(e))
            raise Exception(f"Ошибка синтеза речи: {str(e)}")
    
    def _preprocess_text(self, text: str) -> str:
        """
        Предварительная обработка текста для лучшего синтеза
        
        Args:
            text: Исходный текст
            
        Returns:
            Обработанный текст
        """
        # Удаляем эмодзи и специальные символы
        import re
        
        # Удаляем эмодзи
        text = re.sub(r'[^\w\s\.\,\!\?\:\;\-\(\)\"\']+', '', text)
        
        # Заменяем сокращения
        replacements = {
            "пр-во": "производство",
            "тр-ж": "трикотаж", 
            "и т.д.": "и так далее",
            "и т.п.": "и тому подобное",
            "руб.": "рублей",
            "₽": "рублей",
            "%": "процентов"
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        # Ограничиваем длину предложений
        sentences = text.split('.')
        processed_sentences = []
        
        for sentence in sentences:
            if len(sentence.strip()) > 200:
                # Разбиваем длинные предложения на части
                parts = sentence.split(',')
                current_part = ""
                for part in parts:
                    if len(current_part + part) < 150:
                        current_part += part + ","
                    else:
                        if current_part:
                            processed_sentences.append(current_part.rstrip(','))
                        current_part = part + ","
                if current_part:
                    processed_sentences.append(current_part.rstrip(','))
            else:
                processed_sentences.append(sentence)
        
        result = '. '.join(s.strip() for s in processed_sentences if s.strip())
        return result
    
    async def save_temp_audio(self, audio_data: bytes, filename: Optional[str] = None) -> Path:
        """
        Сохранить аудио во временный файл
        
        Args:
            audio_data: Аудио данные
            filename: Имя файла (опционально)
            
        Returns:
            Путь к сохраненному файлу
        """
        if not filename:
            import uuid
            filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        
        file_path = self.temp_dir / filename
        
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(audio_data)
        
        logger.info("temp_audio_saved", path=str(file_path), size=len(audio_data))
        return file_path
    
    def cleanup_temp_files(self, max_age_hours: int = 24):
        """
        Очистить старые временные файлы
        
        Args:
            max_age_hours: Максимальный возраст файлов в часах
        """
        import time
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        for file_path in self.temp_dir.glob("*.mp3"):
            if current_time - file_path.stat().st_mtime > max_age_seconds:
                try:
                    file_path.unlink()
                    logger.info("temp_file_cleaned", path=str(file_path))
                except Exception as e:
                    logger.warning("temp_file_cleanup_error", path=str(file_path), error=str(e))


# Фабрика для создания TTS сервиса
async def create_tts_service(provider_name: str = "elevenlabs", redis_client=None) -> TextToSpeechService:
    """
    Создать TTS сервис с выбранным провайдером
    
    Args:
        provider_name: Имя провайдера ("elevenlabs", "openai")
        redis_client: Redis клиент для кэширования
        
    Returns:
        Настроенный TTS сервис
    """
    if provider_name.lower() == "elevenlabs":
        # Импортируем новый адаптер
        from adapters.elevenlabs_adapter import create_elevenlabs_adapter
        
        # Создаем фоллбэк провайдер
        fallback_provider = OpenAITTS()
        
        # Создаем оптимизированный ElevenLabs адаптер
        elevenlabs_adapter = await create_elevenlabs_adapter(
            redis_client=redis_client,
            fallback_provider=fallback_provider,
            start_workers=True
        )
        
        return ElevenLabsTextToSpeechService(elevenlabs_adapter)
        
    elif provider_name.lower() == "openai":
        provider = OpenAITTS()
    else:
        raise ValueError(f"Неподдерживаемый TTS провайдер: {provider_name}")
    
    return TextToSpeechService(provider)


class ElevenLabsTextToSpeechService:
    """Сервис TTS с использованием оптимизированного ElevenLabs адаптера"""
    
    def __init__(self, elevenlabs_adapter):
        self.adapter = elevenlabs_adapter
        self.temp_dir = Path(tempfile.gettempdir()) / "ai_seller_tts"
        self.temp_dir.mkdir(exist_ok=True)
    
    async def synthesize_text(self, text: str, voice: str = "alena") -> bytes:
        """
        Синтезировать речь из текста
        
        Args:
            text: Текст для синтеза
            voice: Голос для использования (по умолчанию "alena")
            
        Returns:
            Аудио данные в формате MP3
        """
        try:
            response = await self.adapter.synthesize_speech(text, voice)
            
            logger.info("elevenlabs_synthesis_success", 
                       text_length=len(text), 
                       audio_size=len(response.audio_data),
                       cached=response.cached,
                       voice=response.voice_used)
            
            return response.audio_data
            
        except Exception as e:
            logger.error("elevenlabs_synthesis_error", error=str(e))
            raise Exception(f"Ошибка синтеза речи ElevenLabs: {str(e)}")
    
    async def save_temp_audio(self, audio_data: bytes, filename: Optional[str] = None) -> Path:
        """Сохранить аудио во временный файл"""
        if not filename:
            import uuid
            filename = f"tts_elevenlabs_{uuid.uuid4().hex[:8]}.mp3"
        
        file_path = self.temp_dir / filename
        
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(audio_data)
        
        logger.info("temp_audio_saved", path=str(file_path), size=len(audio_data))
        return file_path
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику использования"""
        return self.adapter.get_stats()
    
    async def cleanup(self):
        """Очистка ресурсов"""
        await self.adapter.stop_workers()