"""
test_metrics.py — Тесты для системы метрик SoVAni AI-продавца.

© SoVAni 2025
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from utils.metrics import (
    MetricsCollector, 
    HealthChecker, 
    MetricType,
    get_metrics_collector,
    get_health_checker,
    track_duration,
    count_calls
)


@pytest.fixture
async def metrics_collector():
    """Фикстура для создания MetricsCollector"""
    collector = MetricsCollector()
    yield collector
    # Cleanup не требуется для тестов


@pytest.fixture
async def health_checker(metrics_collector):
    """Фикстура для создания HealthChecker"""
    checker = HealthChecker(metrics_collector)
    return checker


class TestMetricsCollector:
    """Тесты для MetricsCollector"""
    
    def test_init_base_metrics(self, metrics_collector):
        """Тест инициализации базовых метрик"""
        assert "sovani_ai_seller_telegram_updates_total" in metrics_collector.metrics
        assert "sovani_ai_seller_fsm_state_transitions_total" in metrics_collector.metrics
        assert "sovani_ai_seller_llm_requests_total" in metrics_collector.metrics
        assert "sovani_ai_seller_crm_leads_created_total" in metrics_collector.metrics
        
    def test_increment_counter(self, metrics_collector):
        """Тест увеличения счетчика"""
        metric_name = "telegram_updates_total"
        initial_value = metrics_collector.metrics[f"sovani_ai_seller_{metric_name}"].value
        
        metrics_collector.increment_counter(metric_name, 5.0)
        
        new_value = metrics_collector.metrics[f"sovani_ai_seller_{metric_name}"].value
        assert new_value == initial_value + 5.0
        
    def test_set_gauge(self, metrics_collector):
        """Тест установки gauge метрики"""
        metric_name = "fsm_current_sessions"
        test_value = 42.0
        
        metrics_collector.set_gauge(metric_name, test_value)
        
        stored_value = metrics_collector.metrics[f"sovani_ai_seller_{metric_name}"].value
        assert stored_value == test_value
        
    def test_record_duration(self, metrics_collector):
        """Тест записи времени выполнения"""
        metric_name = "llm_requests_duration_seconds"
        test_duration = 1.5
        
        initial_count = metrics_collector.metrics["sovani_ai_seller_llm_requests_total"].value
        
        metrics_collector.record_duration(metric_name, test_duration)
        
        # Проверяем что время записано
        stored_duration = metrics_collector.metrics[f"sovani_ai_seller_{metric_name}"].value
        assert stored_duration == test_duration
        
        # Проверяем что счетчик увеличился
        new_count = metrics_collector.metrics["sovani_ai_seller_llm_requests_total"].value
        assert new_count == initial_count + 1.0
        
    @pytest.mark.asyncio
    async def test_collect_system_metrics(self, metrics_collector):
        """Тест сбора системных метрик"""
        with patch('psutil.virtual_memory') as mock_memory, \
             patch('psutil.cpu_percent') as mock_cpu:
            
            # Настройка мок объектов
            mock_memory.return_value = MagicMock(used=1024*1024*512)  # 512MB
            mock_cpu.return_value = 25.5
            
            await metrics_collector.collect_system_metrics()
            
            # Проверка что метрики обновились
            memory_metric = metrics_collector.metrics["sovani_ai_seller_system_memory_usage_bytes"]
            assert memory_metric.value == 1024*1024*512
            
            cpu_metric = metrics_collector.metrics["sovani_ai_seller_system_cpu_usage_percent"]
            assert cpu_metric.value == 25.5
            
    def test_format_prometheus_metrics(self, metrics_collector):
        """Тест форматирования в Prometheus формат"""
        # Установим тестовые значения
        metrics_collector.set_gauge("fsm_current_sessions", 10)
        metrics_collector.increment_counter("telegram_updates_total", 100)
        
        prometheus_text = metrics_collector.format_prometheus_metrics()
        
        # Проверка что формат корректный
        assert "# HELP" in prometheus_text
        assert "# TYPE" in prometheus_text
        assert "sovani_ai_seller_fsm_current_sessions 10.0" in prometheus_text
        assert "sovani_ai_seller_telegram_updates_total 100.0" in prometheus_text
        
    def test_get_metrics_json(self, metrics_collector):
        """Тест получения метрик в JSON формате"""
        metrics_collector.set_gauge("fsm_current_sessions", 5)
        
        json_data = metrics_collector.get_metrics_json()
        
        assert "timestamp" in json_data
        assert "namespace" in json_data
        assert json_data["namespace"] == "sovani_ai_seller"
        assert "metrics" in json_data
        assert "sovani_ai_seller_fsm_current_sessions" in json_data["metrics"]
        assert json_data["metrics"]["sovani_ai_seller_fsm_current_sessions"]["value"] == 5


class TestHealthChecker:
    """Тесты для HealthChecker"""
    
    @pytest.mark.asyncio
    async def test_check_redis_health_success(self, health_checker):
        """Тест успешной проверки Redis"""
        with patch.object(health_checker.metrics_collector.redis_client, 'ping', new_callable=AsyncMock) as mock_ping, \
             patch.object(health_checker.metrics_collector.redis_client, 'info', new_callable=AsyncMock) as mock_info:
            
            mock_ping.return_value = True
            mock_info.return_value = {
                'used_memory': 1024*1024,
                'connected_clients': 5,
                'redis_version': '7.0.0'
            }
            
            result = await health_checker.check_redis_health()
            
            assert result.component == "redis"
            assert result.status == "healthy"
            assert result.details['memory_usage_bytes'] == 1024*1024
            assert result.details['connected_clients'] == 5
            assert result.response_time_ms is not None
            
    @pytest.mark.asyncio
    async def test_check_redis_health_failure(self, health_checker):
        """Тест неудачной проверки Redis"""
        with patch.object(health_checker.metrics_collector.redis_client, 'ping', new_callable=AsyncMock) as mock_ping:
            mock_ping.side_effect = Exception("Connection failed")
            
            result = await health_checker.check_redis_health()
            
            assert result.component == "redis"
            assert result.status == "unhealthy"
            assert "Connection failed" in result.message
            
    @pytest.mark.asyncio
    async def test_check_system_health(self, health_checker):
        """Тест проверки системного здоровья"""
        with patch('psutil.virtual_memory') as mock_memory, \
             patch('psutil.disk_usage') as mock_disk, \
             patch('psutil.cpu_percent') as mock_cpu:
            
            # Настройка для здорового состояния
            mock_memory.return_value = MagicMock(percent=50.0, available=2*1024**3)
            mock_disk.return_value = MagicMock(percent=30.0, free=10*1024**3)
            mock_cpu.return_value = 25.0
            
            result = await health_checker.check_system_health()
            
            assert result.component == "system"
            assert result.status == "healthy"
            assert result.details['memory_percent'] == 50.0
            assert result.details['disk_percent'] == 30.0
            assert result.details['cpu_percent'] == 25.0
            
    @pytest.mark.asyncio
    async def test_run_full_health_check(self, health_checker):
        """Тест полной проверки здоровья"""
        with patch.object(health_checker, 'check_system_health') as mock_system, \
             patch.object(health_checker, 'check_redis_health') as mock_redis, \
             patch.object(health_checker.metrics_collector, 'collect_system_metrics', new_callable=AsyncMock) as mock_collect_system, \
             patch.object(health_checker.metrics_collector, 'collect_application_metrics', new_callable=AsyncMock) as mock_collect_app:
            
            # Настройка мок объектов
            mock_system.return_value = MagicMock(status="healthy", component="system", details={}, response_time_ms=10)
            mock_redis.return_value = MagicMock(status="healthy", component="redis", details={}, response_time_ms=5)
            mock_collect_app.return_value = {"active_sessions": 5}
            
            result = await health_checker.run_full_health_check()
            
            assert result["status"] == "healthy"
            assert "components" in result
            assert "system" in result["components"]
            assert "redis" in result["components"]
            assert result["application_metrics"]["active_sessions"] == 5
            assert "total_check_time_ms" in result


class TestDecorators:
    """Тесты для декораторов метрик"""
    
    @pytest.mark.asyncio
    async def test_track_duration_decorator(self):
        """Тест декоратора track_duration"""
        with patch('utils.metrics.get_metrics_collector') as mock_get_collector:
            mock_collector = MagicMock()
            mock_get_collector.return_value = mock_collector
            
            @track_duration("test_duration_seconds")
            async def test_function():
                await asyncio.sleep(0.1)
                return "success"
            
            result = await test_function()
            
            assert result == "success"
            mock_collector.record_duration.assert_called_once()
            
            # Проверяем что время больше 0.1 секунды
            call_args = mock_collector.record_duration.call_args
            assert call_args[0][0] == "test_duration_seconds"
            assert call_args[0][1] >= 0.1
            
    @pytest.mark.asyncio
    async def test_count_calls_decorator(self):
        """Тест декоратора count_calls"""
        with patch('utils.metrics.get_metrics_collector') as mock_get_collector:
            mock_collector = MagicMock()
            mock_get_collector.return_value = mock_collector
            
            @count_calls("test_calls_total")
            async def test_function():
                return "success"
            
            result = await test_function()
            
            assert result == "success"
            mock_collector.increment_counter.assert_called_once_with("test_calls_total", labels=None)
            
    @pytest.mark.asyncio
    async def test_count_calls_decorator_with_error(self):
        """Тест декоратора count_calls при ошибке"""
        with patch('utils.metrics.get_metrics_collector') as mock_get_collector:
            mock_collector = MagicMock()
            mock_get_collector.return_value = mock_collector
            
            @count_calls("test_calls_total")
            async def test_function():
                raise ValueError("Test error")
            
            with pytest.raises(ValueError):
                await test_function()
            
            # Проверяем что был вызван счетчик ошибок
            calls = mock_collector.increment_counter.call_args_list
            assert len(calls) == 1
            assert calls[0][0][0] == "test_calls_errors_total"


@pytest.mark.asyncio
async def test_singleton_functions():
    """Тест singleton функций"""
    collector1 = await get_metrics_collector()
    collector2 = await get_metrics_collector()
    
    assert collector1 is collector2
    
    checker1 = await get_health_checker()
    checker2 = await get_health_checker()
    
    assert checker1 is checker2