"""
Whisper Voice Transcription Service
Использует OpenAI Whisper API для распознавания голосовых сообщений
"""

import os
import aiohttp
import asyncio
import tempfile
import structlog
from typing import Optional, Tuple
from pathlib import Path

logger = structlog.get_logger("services.whisper")

WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"
MAX_FILE_SIZE_MB = 25  # OpenAI limit
SUPPORTED_FORMATS = {"ogg", "mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"}


class WhisperTranscriptionError(Exception):
    """Ошибка транскрипции"""
    pass


class WhisperService:
    """Сервис транскрипции голоса через OpenAI Whisper"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout = timeout
        self.max_retries = max_retries

    def is_available(self) -> bool:
        """Проверяет доступность сервиса"""
        return bool(self.api_key)

    async def transcribe_file(
        self,
        file_path: str,
        language: str = "ru"
    ) -> Tuple[str, float]:
        """
        Транскрибирует аудио файл.

        Args:
            file_path: Путь к аудио файлу
            language: Язык для распознавания (по умолчанию русский)

        Returns:
            Tuple[str, float]: (текст, длительность в секундах)

        Raises:
            WhisperTranscriptionError: При ошибке транскрипции
        """
        if not self.api_key:
            raise WhisperTranscriptionError("OpenAI API key not configured")

        file_path = Path(file_path)
        if not file_path.exists():
            raise WhisperTranscriptionError(f"File not found: {file_path}")

        # Проверяем размер файла
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            raise WhisperTranscriptionError(
                f"File too large: {file_size_mb:.1f}MB (max {MAX_FILE_SIZE_MB}MB)"
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        attempt = 0
        last_error = None

        while attempt < self.max_retries:
            try:
                start_time = asyncio.get_event_loop().time()

                # Формируем multipart запрос
                data = aiohttp.FormData()
                data.add_field(
                    "file",
                    open(file_path, "rb"),
                    filename=file_path.name,
                    content_type="audio/ogg"
                )
                data.add_field("model", "whisper-1")
                data.add_field("language", language)
                data.add_field("response_format", "verbose_json")

                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as session:
                    async with session.post(
                        WHISPER_API_URL,
                        headers=headers,
                        data=data
                    ) as resp:
                        latency = asyncio.get_event_loop().time() - start_time

                        if resp.status != 200:
                            error_data = await resp.json()
                            error_msg = error_data.get("error", {}).get(
                                "message", str(error_data)
                            )
                            logger.error(
                                "whisper_api_error",
                                status=resp.status,
                                error=error_msg,
                                attempt=attempt + 1
                            )

                            # Retry on server errors
                            if resp.status >= 500:
                                attempt += 1
                                await asyncio.sleep(2 ** attempt)
                                continue

                            raise WhisperTranscriptionError(
                                f"Whisper API error: {error_msg}"
                            )

                        result = await resp.json()
                        text = result.get("text", "").strip()
                        duration = result.get("duration", 0.0)

                        logger.info(
                            "whisper_transcription_success",
                            text_length=len(text),
                            duration=duration,
                            latency_ms=latency * 1000,
                            language=language
                        )

                        return text, duration

            except asyncio.TimeoutError:
                logger.warning(
                    "whisper_timeout",
                    attempt=attempt + 1,
                    max_retries=self.max_retries
                )
                last_error = "Request timeout"
                attempt += 1
                await asyncio.sleep(2 ** attempt)

            except aiohttp.ClientError as e:
                logger.warning(
                    "whisper_network_error",
                    error=str(e),
                    attempt=attempt + 1
                )
                last_error = str(e)
                attempt += 1
                await asyncio.sleep(2 ** attempt)

            except Exception as e:
                logger.exception("whisper_unexpected_error")
                raise WhisperTranscriptionError(f"Unexpected error: {str(e)}")

        raise WhisperTranscriptionError(
            f"Failed after {self.max_retries} attempts: {last_error}"
        )

    async def transcribe_telegram_voice(
        self,
        bot,
        voice_file_id: str,
        language: str = "ru"
    ) -> Tuple[str, float]:
        """
        Скачивает и транскрибирует голосовое сообщение из Telegram.

        Args:
            bot: Telegram Bot instance
            voice_file_id: Telegram file_id голосового сообщения
            language: Язык для распознавания

        Returns:
            Tuple[str, float]: (текст, длительность в секундах)
        """
        # Получаем информацию о файле
        file = await bot.get_file(voice_file_id)

        # Создаём временный файл
        with tempfile.NamedTemporaryFile(
            suffix=".ogg",
            delete=False
        ) as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Скачиваем файл
            await file.download_to_drive(tmp_path)

            logger.info(
                "voice_file_downloaded",
                file_id=voice_file_id,
                path=tmp_path
            )

            # Транскрибируем
            text, duration = await self.transcribe_file(tmp_path, language)
            return text, duration

        finally:
            # Удаляем временный файл
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# Синглтон для использования в приложении
_whisper_service: Optional[WhisperService] = None


def get_whisper_service() -> WhisperService:
    """Получить singleton экземпляр WhisperService"""
    global _whisper_service
    if _whisper_service is None:
        _whisper_service = WhisperService()
    return _whisper_service
