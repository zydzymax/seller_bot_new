"""
test_elevenlabs_adapter.py — Тесты для ElevenLabsAdapter

Проверяет:
- Синтез речи с голосом "Алена"
- Кэширование аудио
- Обработку ошибок и фоллбэки
- Worker'ы и очереди
- VPN устойчивость

© SoVAni 2025
"""

import pytest
import asyncio
import aiohttp
from unittest.mock import Mock, patch, AsyncMock
import json
import tempfile
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from adapters.elevenlabs_adapter import (
    ElevenLabsAdapter, TTSRequest, TTSResponse, ElevenLabsError,
    VoiceManager, AudioCache, create_elevenlabs_adapter
)


class TestVoiceManager:
    """Тесты менеджера голосов"""
    
    def test_alena_config(self):
        """Тест конфигурации голоса Алены"""
        config = VoiceManager.get_alena_config()
        
        assert config["name"] == "Алена"
        assert "voice_id" in config
        assert "settings" in config
        assert config["settings"]["stability"] == 0.65
        assert config["settings"]["similarity_boost"] == 0.75
    
    def test_fallback_voices(self):
        """Тест фоллбэк голосов"""
        voice = VoiceManager.get_fallback_voice(0)
        assert "voice_id" in voice
        assert "name" in voice
        
        # Тест выхода за границы
        voice = VoiceManager.get_fallback_voice(999)
        assert voice == VoiceManager.get_fallback_voice(0)


class TestAudioCache:
    """Тесты кэша аудио"""
    
    @pytest.fixture
    def cache(self):
        """Фикстура кэша без Redis"""
        return AudioCache(redis_client=None, cache_ttl=3600)
    
    @pytest.mark.asyncio
    async def test_memory_cache(self, cache):
        """Тест кэша в памяти"""
        text = "Привет от Алены!"
        voice_id = "test_voice"
        settings = {"stability": 0.5}
        audio_data = b"fake_audio_data"
        
        # Сначала пусто
        cached = await cache.get(text, voice_id, settings)
        assert cached is None
        
        # Сохраняем
        await cache.set(text, voice_id, settings, audio_data)
        
        # Проверяем
        cached = await cache.get(text, voice_id, settings)
        assert cached == audio_data
    
    @pytest.mark.asyncio
    async def test_cache_key_uniqueness(self, cache):
        """Тест уникальности ключей кэша"""
        text1, text2 = "Текст 1", "Текст 2" 
        voice_id = "voice"
        settings = {"stability": 0.5}
        
        key1 = cache._get_cache_key(text1, voice_id, settings)
        key2 = cache._get_cache_key(text2, voice_id, settings)
        
        assert key1 != key2
        
        # Одинаковые параметры = одинаковые ключи
        key3 = cache._get_cache_key(text1, voice_id, settings)
        assert key1 == key3


class TestElevenLabsAdapter:
    """Тесты основного адаптера"""
    
    @pytest.fixture
    def mock_redis(self):
        """Мок Redis клиента"""
        redis_mock = AsyncMock()
        redis_mock.get.return_value = None
        redis_mock.setex.return_value = True
        return redis_mock
    
    @pytest.fixture
    def fallback_provider(self):
        """Мок фоллбэк провайдера"""
        provider = Mock()
        provider.synthesize = AsyncMock(return_value=b"fallback_audio")
        return provider
    
    @pytest.fixture
    def adapter(self, mock_redis, fallback_provider):
        """Фикстура адаптера для тестов"""
        return ElevenLabsAdapter(
            api_key="test_key",
            redis_client=mock_redis,
            fallback_provider=fallback_provider,
            max_workers=1
        )
    
    def test_adapter_initialization(self, adapter):
        """Тест инициализации адаптера"""
        assert adapter.api_key == "test_key"
        assert adapter.max_workers == 1
        assert adapter.cache is not None
        assert adapter.voice_manager is not None
        assert not adapter.is_running
    
    def test_text_preprocessing(self, adapter):
        """Тест предобработки текста"""
        # Длинный текст
        long_text = "А" * 3000
        processed = adapter._preprocess_text(long_text)
        assert len(processed) <= 2400
        assert processed.endswith("...")
        
        # Замены
        text_with_symbols = "Цена 100₽, 50% скидка, № заказа"
        processed = adapter._preprocess_text(text_with_symbols)
        assert "рублей" in processed
        assert "процентов" in processed
        assert "номер" in processed
        
        # Эмодзи
        text_with_emoji = "Привет! 😊 Как дела? 🎉"
        processed = adapter._preprocess_text(text_with_emoji)
        assert "😊" not in processed
        assert "🎉" not in processed
    
    def test_voice_id_resolution(self, adapter):
        """Тест преобразования имен голосов в ID"""
        # Алена
        voice_id = adapter._resolve_voice_id("alena")
        alena_config = VoiceManager.get_alena_config()
        assert voice_id == alena_config["voice_id"]
        
        # ID напрямую
        direct_id = "direct_voice_id_12345"
        assert adapter._resolve_voice_id(direct_id) == direct_id
        
        # Неизвестный голос -> фоллбэк
        fallback_id = adapter._resolve_voice_id("unknown_voice")
        fallback_voice = VoiceManager.get_fallback_voice(0)
        assert fallback_id == fallback_voice["voice_id"]
    
    @pytest.mark.asyncio
    async def test_worker_lifecycle(self, adapter):
        """Тест жизненного цикла worker'ов"""
        assert not adapter.is_running
        assert len(adapter.workers) == 0
        
        # Запуск
        await adapter.start_workers()
        assert adapter.is_running
        assert len(adapter.workers) == adapter.max_workers
        
        # Остановка
        await adapter.stop_workers()
        assert not adapter.is_running
    
    @pytest.mark.asyncio
    async def test_synthesis_with_cache_hit(self, adapter):
        """Тест синтеза с попаданием в кэш"""
        text = "Привет от Алены!"
        cached_audio = b"cached_audio_data"
        
        # Настроим кэш для возврата данных
        adapter.cache.get = AsyncMock(return_value=cached_audio)
        
        response = await adapter.synthesize_speech(text)
        
        assert response.audio_data == cached_audio
        assert response.cached == True
        assert response.voice_used == "alena"
        
        # Статистика
        stats = adapter.get_stats()
        assert stats["requests_total"] == 1
        assert stats["requests_cached"] == 1
    
    @pytest.mark.asyncio
    async def test_synthesis_api_success(self, adapter):
        """Тест успешного API вызова"""
        text = "Тест синтеза речи"
        mock_audio = b"mock_audio_response"
        
        # Настроим кэш как пустой
        adapter.cache.get = AsyncMock(return_value=None)
        adapter.cache.set = AsyncMock()
        
        # Мокаем HTTP ответ
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.read.return_value = mock_audio
            
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            response = await adapter.synthesize_speech(text)
            
            assert response.audio_data == mock_audio
            assert not response.cached
            assert response.provider == "elevenlabs"
            
            # Проверяем что кэш был обновлен
            adapter.cache.set.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_synthesis_with_fallback(self, adapter):
        """Тест фоллбэка на OpenAI при ошибке"""
        text = "Тест фоллбэка"
        
        adapter.cache.get = AsyncMock(return_value=None)
        
        # Мокаем ошибку API
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.side_effect = Exception("API Error")
            
            response = await adapter.synthesize_speech(text)
            
            # Должен вернуться фоллбэк
            assert response.audio_data == b"fallback_audio"
            assert response.provider == "openai_fallback"
            
            # Проверяем что фоллбэк был вызван
            adapter.fallback_provider.synthesize.assert_called_once_with(text, "nova")
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self, adapter):
        """Тест обработки различных ошибок API"""
        text = "Тест ошибок"
        adapter.cache.get = AsyncMock(return_value=None)
        
        # Тест 429 Rate Limit
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 429
            mock_response.text.return_value = "Rate limit exceeded"
            
            session_mock = mock_session.return_value.__aenter__.return_value
            session_mock.post.return_value.__aenter__.return_value = mock_response
            
            # Должно выбросить исключение после всех попыток
            with pytest.raises(ElevenLabsError):
                await adapter._process_tts_request(
                    TTSRequest(text=text, voice_id="test_voice")
                )
    
    @pytest.mark.asyncio
    async def test_get_voices(self, adapter):
        """Тест получения списка голосов"""
        mock_voices = [
            {"voice_id": "voice1", "name": "Voice 1"},
            {"voice_id": "voice2", "name": "Voice 2"}
        ]
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json.return_value = {"voices": mock_voices}
            
            session_mock = mock_session.return_value.__aenter__.return_value
            session_mock.get.return_value.__aenter__.return_value = mock_response
            
            voices = await adapter.get_voices()
            assert voices == mock_voices
    
    def test_stats_tracking(self, adapter):
        """Тест отслеживания статистики"""
        initial_stats = adapter.get_stats()
        
        assert "requests_total" in initial_stats
        assert "requests_cached" in initial_stats
        assert "requests_failed" in initial_stats
        assert "queue_size" in initial_stats
        assert "workers_active" in initial_stats


class TestIntegration:
    """Интеграционные тесты"""
    
    @pytest.mark.asyncio
    async def test_create_adapter_factory(self):
        """Тест фабрики создания адаптера"""
        mock_redis = AsyncMock()
        mock_fallback = Mock()
        
        adapter = await create_elevenlabs_adapter(
            api_key="test_key",
            redis_client=mock_redis,
            fallback_provider=mock_fallback,
            start_workers=False
        )
        
        assert isinstance(adapter, ElevenLabsAdapter)
        assert adapter.api_key == "test_key"
        assert not adapter.is_running  # workers не запущены
    
    @pytest.mark.asyncio
    async def test_full_synthesis_pipeline(self):
        """Тест полного пайплайна синтеза"""
        # Этот тест требует реального API ключа, поэтому пропускаем
        pytest.skip("Requires real API key")
        
        adapter = await create_elevenlabs_adapter(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            start_workers=True
        )
        
        try:
            response = await adapter.synthesize_speech(
                "Привет! Меня зовут Алена, и я помогу вам с заказом.",
                voice="alena"
            )
            
            assert len(response.audio_data) > 0
            assert response.voice_used == "alena"
            
            stats = adapter.get_stats()
            assert stats["requests_total"] > 0
            
        finally:
            await adapter.stop_workers()


if __name__ == "__main__":
    # Запуск тестов
    pytest.main([__file__, "-v"])