"""
audio_config.py — Конфигурация аудио сервисов для AI-продавца
© SoVAni 2025
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class STTConfig:
    """Конфигурация для Speech-to-Text"""
    provider: str = "openai"
    model: str = "whisper-1"
    language: str = "ru"
    max_duration_seconds: int = 120
    max_audio_duration_seconds: int = 300
    api_key_env: str = "OPENAI_API_KEY"


@dataclass
class TTSConfig:
    """Конфигурация для Text-to-Speech"""
    provider: str = "openai"
    model: str = "tts-1"
    voice: str = "nova"
    response_format: str = "mp3"
    api_key_env: str = "OPENAI_API_KEY"


@dataclass
class ElevenLabsConfig:
    """Конфигурация для ElevenLabs"""
    enabled: bool = False
    api_key_env: str = "ELEVENLABS_API_KEY"
    default_voice: str = "rachel"
    model: str = "eleven_multilingual_v2"


@dataclass
class AudioProcessingConfig:
    """Конфигурация обработки аудио"""
    temp_dir: str = "/tmp/ai_seller_audio"
    cleanup_interval_hours: int = 24
    max_file_size_mb: int = 25


@dataclass
class AudioConfig:
    """Общая конфигурация аудио сервисов"""
    stt: STTConfig
    tts: TTSConfig
    elevenlabs: ElevenLabsConfig
    processing: AudioProcessingConfig


class AudioConfigManager:
    """Менеджер конфигурации аудио сервисов"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._get_default_config_path()
        self.config = self._load_config()
    
    def _get_default_config_path(self) -> str:
        """Получить путь к конфигурационному файлу по умолчанию"""
        current_dir = Path(__file__).parent
        return str(current_dir / "orchestrator.yaml")
    
    def _load_config(self) -> AudioConfig:
        """Загрузить конфигурацию из YAML файла"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            
            audio_config = config_data.get('audio', {})
            
            # STT конфигурация
            stt_data = audio_config.get('speech_to_text', {})
            stt_config = STTConfig(
                provider=stt_data.get('provider', 'openai'),
                model=stt_data.get('model', 'whisper-1'),
                language=stt_data.get('language', 'ru'),
                max_duration_seconds=stt_data.get('max_duration_seconds', 120),
                max_audio_duration_seconds=stt_data.get('max_audio_duration_seconds', 300),
                api_key_env=stt_data.get('api_key_env', 'OPENAI_API_KEY')
            )
            
            # TTS конфигурация
            tts_data = audio_config.get('text_to_speech', {})
            tts_config = TTSConfig(
                provider=tts_data.get('provider', 'openai'),
                model=tts_data.get('model', 'tts-1'),
                voice=tts_data.get('voice', 'nova'),
                response_format=tts_data.get('response_format', 'mp3'),
                api_key_env=tts_data.get('api_key_env', 'OPENAI_API_KEY')
            )
            
            # ElevenLabs конфигурация
            elevenlabs_data = audio_config.get('elevenlabs', {})
            elevenlabs_config = ElevenLabsConfig(
                enabled=elevenlabs_data.get('enabled', False),
                api_key_env=elevenlabs_data.get('api_key_env', 'ELEVENLABS_API_KEY'),
                default_voice=elevenlabs_data.get('default_voice', 'rachel'),
                model=elevenlabs_data.get('model', 'eleven_multilingual_v2')
            )
            
            # Обработка аудио конфигурация
            processing_data = audio_config.get('processing', {})
            processing_config = AudioProcessingConfig(
                temp_dir=processing_data.get('temp_dir', '/tmp/ai_seller_audio'),
                cleanup_interval_hours=processing_data.get('cleanup_interval_hours', 24),
                max_file_size_mb=processing_data.get('max_file_size_mb', 25)
            )
            
            return AudioConfig(
                stt=stt_config,
                tts=tts_config,
                elevenlabs=elevenlabs_config,
                processing=processing_config
            )
            
        except Exception as e:
            # Если конфигурация не загрузилась, используем значения по умолчанию
            return AudioConfig(
                stt=STTConfig(),
                tts=TTSConfig(),
                elevenlabs=ElevenLabsConfig(),
                processing=AudioProcessingConfig()
            )
    
    def get_stt_api_key(self) -> Optional[str]:
        """Получить API ключ для STT провайдера"""
        return os.getenv(self.config.stt.api_key_env)
    
    def get_tts_api_key(self) -> Optional[str]:
        """Получить API ключ для TTS провайдера"""
        return os.getenv(self.config.tts.api_key_env)
    
    def get_elevenlabs_api_key(self) -> Optional[str]:
        """Получить API ключ для ElevenLabs"""
        return os.getenv(self.config.elevenlabs.api_key_env)
    
    def is_stt_enabled(self) -> bool:
        """Проверить, включен ли STT"""
        return bool(self.get_stt_api_key())
    
    def is_tts_enabled(self) -> bool:
        """Проверить, включен ли TTS"""
        return bool(self.get_tts_api_key())
    
    def is_elevenlabs_enabled(self) -> bool:
        """Проверить, включен ли ElevenLabs"""
        return self.config.elevenlabs.enabled and bool(self.get_elevenlabs_api_key())


# Глобальный экземпляр конфигурации
_audio_config = None


def get_audio_config() -> AudioConfigManager:
    """Получить глобальный экземпляр конфигурации аудио"""
    global _audio_config
    if _audio_config is None:
        _audio_config = AudioConfigManager()
    return _audio_config


# Константы для удобства
def get_available_tts_voices() -> Dict[str, str]:
    """Получить список доступных голосов для TTS"""
    return {
        "alloy": "Нейтральный голос (Alloy)",
        "echo": "Мужской голос (Echo)",
        "fable": "Британский мужской голос (Fable)",
        "onyx": "Глубокий мужской голос (Onyx)",
        "nova": "Женский голос (Nova)",
        "shimmer": "Мягкий женский голос (Shimmer)"
    }


def get_available_elevenlabs_voices() -> Dict[str, str]:
    """Получить список доступных голосов для ElevenLabs"""
    return {
        "rachel": "Rachel (Женский американский)",
        "domi": "Domi (Женский американский)",
        "bella": "Bella (Женский американский)",
        "antoni": "Antoni (Мужской американский)",
        "elli": "Elli (Женский американский)",
        "josh": "Josh (Мужской американский)"
    }