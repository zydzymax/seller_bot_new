"""
speech_to_text.py — Модуль распознавания речи для AI-продавца
Поддерживает OpenAI Whisper и другие STT провайдеры
© SoVAni 2025
"""

import os
import io
import tempfile
import structlog
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import aiohttp
import aiofiles
from telegram import Bot

logger = structlog.get_logger("ai_seller.speech_to_text")


class STTProvider(ABC):
    """Абстрактный базовый класс для провайдеров распознавания речи"""
    
    @abstractmethod
    async def transcribe(self, audio_file: bytes, audio_format: str = "ogg") -> str:
        """Распознать речь из аудио файла"""
        pass


class OpenAIWhisperSTT(STTProvider):
    """OpenAI Whisper STT провайдер"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"
        
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
    
    async def transcribe(self, audio_file: bytes, audio_format: str = "ogg") -> str:
        """
        Транскрибировать аудио используя OpenAI Whisper API
        
        Args:
            audio_file: Байты аудио файла
            audio_format: Формат аудио (ogg, mp3, wav, etc.)
            
        Returns:
            Распознанный текст
        """
        try:
            # Подготавливаем multipart/form-data запрос
            data = aiohttp.FormData()
            data.add_field('file', audio_file, filename=f'audio.{audio_format}', content_type=f'audio/{audio_format}')
            data.add_field('model', 'whisper-1')
            data.add_field('language', 'ru')  # Русский язык по умолчанию
            data.add_field('response_format', 'text')
            
            headers = {
                'Authorization': f'Bearer {self.api_key}'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/audio/transcriptions",
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status == 200:
                        transcript = await response.text()
                        logger.info("whisper_transcription_success", length=len(transcript))
                        return transcript.strip()
                    else:
                        error_text = await response.text()
                        logger.error("whisper_transcription_error", 
                                   status=response.status, error=error_text)
                        raise Exception(f"Whisper API error: {response.status} {error_text}")
                        
        except Exception as e:
            logger.error("whisper_transcription_exception", error=str(e))
            raise Exception(f"Ошибка распознавания речи: {str(e)}")


class SpeechToTextService:
    """Сервис распознавания речи с поддержкой нескольких провайдеров"""
    
    def __init__(self, provider: STTProvider):
        self.provider = provider
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        
    async def download_telegram_voice(self, file_id: str) -> bytes:
        """
        Скачать голосовое сообщение из Telegram
        
        Args:
            file_id: ID файла в Telegram
            
        Returns:
            Байты аудио файла
        """
        try:
            bot = Bot(token=self.telegram_bot_token)
            file = await bot.get_file(file_id)
            
            # Строим полный URL к файлу
            file_url = f"https://api.telegram.org/file/bot{self.telegram_bot_token}/{file.file_path}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info("telegram_audio_downloaded", 
                                  file_id=file_id, size=len(audio_data))
                        return audio_data
                    else:
                        raise Exception(f"Failed to download file: {response.status}")
                        
        except Exception as e:
            logger.error("telegram_audio_download_error", file_id=file_id, error=str(e))
            raise Exception(f"Ошибка скачивания аудио: {str(e)}")
    
    async def transcribe_telegram_voice(self, file_id: str, duration: int) -> str:
        """
        Распознать голосовое сообщение из Telegram
        
        Args:
            file_id: ID голосового файла в Telegram
            duration: Длительность в секундах
            
        Returns:
            Распознанный текст
        """
        # Проверяем длительность (ограничиваем слишком длинные записи)
        if duration > 120:  # 2 минуты максимум
            return "⚠️ Голосовое сообщение слишком длинное (более 2 минут). Пожалуйста, отправьте более короткое сообщение."
        
        try:
            # Скачиваем аудио
            audio_data = await self.download_telegram_voice(file_id)
            
            # Распознаем речь
            transcript = await self.provider.transcribe(audio_data, "ogg")
            
            if not transcript or len(transcript.strip()) < 3:
                return "⚠️ Не удалось распознать речь. Попробуйте говорить четче."
            
            logger.info("voice_transcription_success", 
                       file_id=file_id, transcript_length=len(transcript))
            return transcript
            
        except Exception as e:
            logger.error("voice_transcription_error", file_id=file_id, error=str(e))
            return "⚠️ Произошла ошибка при распознавании речи. Попробуйте еще раз."
    
    async def transcribe_telegram_audio(self, file_id: str, duration: int) -> str:
        """
        Распознать аудио файл из Telegram
        
        Args:
            file_id: ID аудио файла в Telegram  
            duration: Длительность в секундах
            
        Returns:
            Распознанный текст
        """
        # Проверяем длительность
        if duration > 300:  # 5 минут максимум для аудио
            return "⚠️ Аудио файл слишком длинный (более 5 минут). Пожалуйста, отправьте более короткий файл."
        
        try:
            # Скачиваем аудио
            audio_data = await self.download_telegram_voice(file_id)
            
            # Распознаем речь (предполагаем mp3 формат для аудио)
            transcript = await self.provider.transcribe(audio_data, "mp3")
            
            if not transcript or len(transcript.strip()) < 3:
                return "⚠️ Не удалось распознать речь в аудио файле."
            
            logger.info("audio_transcription_success",
                       file_id=file_id, transcript_length=len(transcript))
            return transcript
            
        except Exception as e:
            logger.error("audio_transcription_error", file_id=file_id, error=str(e))
            return "⚠️ Произошла ошибка при распознавании аудио. Попробуйте еще раз."


# Фабрика для создания STT сервиса
def create_stt_service(provider_name: str = "openai") -> SpeechToTextService:
    """
    Создать STT сервис с выбранным провайдером
    
    Args:
        provider_name: Имя провайдера ("openai", "google", etc.)
        
    Returns:
        Настроенный STT сервис
    """
    if provider_name.lower() == "openai":
        provider = OpenAIWhisperSTT()
    else:
        raise ValueError(f"Неподдерживаемый STT провайдер: {provider_name}")
    
    return SpeechToTextService(provider)