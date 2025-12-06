"""
metrics.py — Prometheus метрики для SoVAni AI-продавца.

- Метрики обработки Telegram сообщений
- LLM использование и производительность  
- CRM интеграция и health статусы
- FSM переходы и состояния
- Rate limiting и DLQ метрики

© SoVAni 2025
"""

import os
import time
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import psutil

import redis.asyncio as redis
from prometheus_client import (
    Counter, Gauge, Histogram, CollectorRegistry, 
    generate_latest, CONTENT_TYPE_LATEST
)
from utils.logging import get_logger, log_performance

logger = get_logger(__name__)


class MetricType(Enum):
    """Типы метрик"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class Metric:
    """Базовая метрика"""
    name: str
    type: MetricType
    description: str
    value: float = 0.0
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass 
class HealthCheck:
    """Результат проверки здоровья компонента"""
    component: str
    status: str  # healthy, degraded, unhealthy
    message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    response_time_ms: Optional[float] = None


class PrometheusMetricsCollector:
    """Prometheus-совместимый сборщик метрик для SoVAni AI seller"""
    
    def __init__(self, redis_url: str = None, registry: Optional[CollectorRegistry] = None):
        self.redis_client = redis.from_url(redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0'))
        self.app_start_time = time.time()
        self.namespace = "sovani_ai_seller"
        
        # Prometheus registry
        self.registry = registry or CollectorRegistry()
        
        # Инициализация Prometheus метрик
        self._init_prometheus_metrics()
        
        # Кэш для системных метрик (обновляем не чаще раза в 30 секунд)
        self._last_system_update = 0
        
    def _init_prometheus_metrics(self):
        """Инициализация Prometheus метрик"""
        # System метрики
        self.system_cpu_usage = Gauge(
            'system_cpu_usage_percent', 
            'System CPU usage percentage',
            registry=self.registry
        )
        
        self.system_memory_usage = Gauge(
            'system_memory_usage_bytes', 
            'System memory usage in bytes',
            registry=self.registry
        )
        
        self.system_memory_usage_percent = Gauge(
            'system_memory_usage_percent', 
            'System memory usage percentage',
            registry=self.registry
        )
        
        # Application метрики
        self.telegram_updates_total = Counter(
            'telegram_updates_total',
            'Total Telegram updates processed',
            registry=self.registry
        )
        
        self.telegram_updates_errors = Counter(
            'telegram_updates_errors_total',
            'Total Telegram update processing errors',
            registry=self.registry
        )
        
        self.telegram_updates_duration = Histogram(
            'telegram_updates_duration_seconds',
            'Telegram update processing duration',
            registry=self.registry
        )
        
        # LLM метрики
        self.llm_requests_total = Counter(
            'llm_requests_total',
            'Total LLM API requests',
            ['provider', 'model', 'status'],
            registry=self.registry
        )
        
        self.llm_request_duration = Histogram(
            'llm_request_duration_seconds',
            'LLM request duration in seconds',
            ['provider', 'model'],
            registry=self.registry
        )
        
        self.llm_tokens_total = Counter(
            'llm_tokens_total',
            'Total LLM tokens used',
            ['provider', 'model', 'type'],  # type: input/output/total
            registry=self.registry
        )
        
        self.llm_cost_total = Counter(
            'llm_cost_total_rub',
            'Total LLM cost in RUB',
            ['provider', 'model'],
            registry=self.registry
        )
        
        # Rate limiting метрики
        self.rate_limit_checks_total = Counter(
            'rate_limit_checks_total',
            'Total rate limit checks',
            ['key_type', 'result'],  # result: allowed/denied
            registry=self.registry
        )
        
        # Circuit breaker метрики
        self.circuit_breaker_state = Gauge(
            'circuit_breaker_state',
            'Circuit breaker state (0=closed, 1=open, 2=half_open)',
            ['model'],
            registry=self.registry
        )
        
        self.circuit_breaker_failures = Counter(
            'circuit_breaker_failures_total',
            'Circuit breaker failure count',
            ['model'],
            registry=self.registry
        )
        
        # Idempotency метрики
        self.idempotency_duplicates_total = Counter(
            'idempotency_duplicates_total',
            'Total duplicate requests detected',
            registry=self.registry
        )
        
        # FSM метрики
        self.fsm_state_transitions_total = Counter(
            'fsm_state_transitions_total',
            'FSM state transitions count',
            ['from_state', 'to_state'],
            registry=self.registry
        )
        
        self.fsm_current_sessions = Gauge(
            'fsm_current_sessions',
            'Current active FSM sessions',
            registry=self.registry
        )
        
        # CRM метрики
        self.crm_operations_total = Counter(
            'crm_operations_total',
            'Total CRM operations',
            ['operation', 'status'],
            registry=self.registry
        )
        
        self.crm_dlq_size = Gauge(
            'crm_dlq_size',
            'CRM DLQ size',
            registry=self.registry
        )
        
    def _init_base_metrics(self):
        """Инициализация базовых метрик системы"""
        base_metrics = [
            # Telegram webhook метрики
            ("telegram_updates_total", MetricType.COUNTER, "Общее количество обработанных Telegram updates"),
            ("telegram_updates_duration_seconds", MetricType.HISTOGRAM, "Время обработки Telegram updates в секундах"),
            ("telegram_updates_errors_total", MetricType.COUNTER, "Количество ошибок обработки Telegram updates"),
            
            # FSM метрики
            ("fsm_state_transitions_total", MetricType.COUNTER, "Количество переходов FSM по состояниям"),
            ("fsm_current_sessions", MetricType.GAUGE, "Текущее количество активных сессий FSM"),
            ("fsm_validation_errors_total", MetricType.COUNTER, "Количество ошибок валидации FSM"),
            
            # LLM метрики
            ("llm_requests_total", MetricType.COUNTER, "Общее количество запросов к LLM"),
            ("llm_requests_duration_seconds", MetricType.HISTOGRAM, "Время выполнения LLM запросов"),
            ("llm_tokens_consumed_total", MetricType.COUNTER, "Общее количество потребленных токенов"),
            ("llm_errors_total", MetricType.COUNTER, "Количество ошибок LLM"),
            
            # CRM метрики
            ("crm_leads_created_total", MetricType.COUNTER, "Количество созданных лидов в CRM"),
            ("crm_contacts_created_total", MetricType.COUNTER, "Количество созданных контактов в CRM"),
            ("crm_requests_duration_seconds", MetricType.HISTOGRAM, "Время выполнения CRM запросов"),
            ("crm_dlq_size", MetricType.GAUGE, "Размер DLQ в CRM адаптере"),
            ("crm_errors_total", MetricType.COUNTER, "Количество ошибок CRM"),
            
            # Rate limiting метрики
            ("rate_limit_blocked_total", MetricType.COUNTER, "Количество заблокированных запросов rate limiter"),
            ("rate_limit_current_usage", MetricType.GAUGE, "Текущее использование rate limiter"),
            
            # Idempotency метрики
            ("idempotency_duplicates_total", MetricType.COUNTER, "Количество обнаруженных дубликатов"),
            ("idempotency_operations_total", MetricType.COUNTER, "Общее количество идемпотентных операций"),
            
            # Системные метрики
            ("system_memory_usage_bytes", MetricType.GAUGE, "Использование памяти системой"),
            ("system_cpu_usage_percent", MetricType.GAUGE, "Использование CPU системой"),
            ("redis_connected", MetricType.GAUGE, "Статус подключения к Redis"),
            ("app_uptime_seconds", MetricType.GAUGE, "Время работы приложения в секундах"),
        ]
        
        for name, metric_type, description in base_metrics:
            self.metrics[name] = Metric(
                name=f"{self.namespace}_{name}",
                type=metric_type,
                description=description
            )
            
    def increment_counter(self, metric_name: str, labels: Dict[str, str] = None, value: float = 1):
        """Увеличение счетчика с поддержкой Prometheus метрик"""
        try:
            metric = getattr(self, metric_name, None)
            if metric and hasattr(metric, 'labels'):
                if labels:
                    metric.labels(**labels).inc(value)
                else:
                    metric.inc(value)
            elif metric and hasattr(metric, 'inc'):
                metric.inc(value)
            else:
                logger.warning(f"Counter {metric_name} not found")
                
            # Логирование performance метрики
            if 'duration' in metric_name or 'processing_time' in metric_name:
                log_performance(f"counter_{metric_name}", int(value * 1000), labels=labels or {})
                
        except Exception as e:
            logger.error(f"Error incrementing counter {metric_name}: {e}")
        
    def set_gauge(self, metric_name: str, value: float, labels: Dict[str, str] = None):
        """Установка значения gauge с поддержкой Prometheus"""
        try:
            metric = getattr(self, metric_name, None)
            if metric and hasattr(metric, 'labels'):
                if labels:
                    metric.labels(**labels).set(value)
                else:
                    metric.set(value)
            elif metric and hasattr(metric, 'set'):
                metric.set(value)
            else:
                logger.warning(f"Gauge {metric_name} not found")
                
        except Exception as e:
            logger.error(f"Error setting gauge {metric_name}: {e}")
        
    def observe_histogram(self, metric_name: str, value: float, labels: Dict[str, str] = None):
        """Добавление наблюдения в histogram"""
        try:
            metric = getattr(self, metric_name, None)
            if metric and hasattr(metric, 'labels'):
                if labels:
                    metric.labels(**labels).observe(value)
                else:
                    metric.observe(value)
            elif metric and hasattr(metric, 'observe'):
                metric.observe(value)
            else:
                logger.warning(f"Histogram {metric_name} not found")
                
            # Логирование performance метрики
            log_performance(f"histogram_{metric_name}", int(value * 1000), labels=labels or {})
            
        except Exception as e:
            logger.error(f"Error observing histogram {metric_name}: {e}")
        
    async def collect_system_metrics(self):
        """Сбор системных метрик с кэшированием"""
        now = time.time()
        
        if now - self._last_system_update < 30:  # 30 секунд кэш
            return
            
        try:
            # CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self.set_gauge('system_cpu_usage_percent', cpu_percent)
            
            # Memory
            memory = psutil.virtual_memory()
            self.set_gauge('system_memory_usage_bytes', memory.used)
            self.set_gauge('system_memory_usage_percent', memory.percent)
            
            # Uptime приложения
            uptime = now - self.app_start_time
            self.set_gauge('app_uptime_seconds', uptime)
            
            self._last_system_update = now
            
            # Логирование системных метрик как performance
            log_performance(
                "system_metrics_collected", 
                int((time.time() - now) * 1000),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent
            )
            
        except Exception as e:
            logger.error(f"Error collecting system metrics: {e}")
            
    async def collect_application_metrics(self) -> Dict[str, Any]:
        """Сбор метрик приложения из Redis"""
        app_metrics = {}
        
        try:
            # FSM активные сессии
            pattern = "dialog:context:*"
            keys = await self.redis_client.keys(pattern)
            self.set_gauge("fsm_current_sessions", len(keys))
            app_metrics["active_sessions"] = len(keys)
            
            # CRM DLQ размер
            dlq_size = await self.redis_client.llen("crm:dlq")
            self.set_gauge("crm_dlq_size", dlq_size)
            app_metrics["crm_dlq_size"] = dlq_size
            
            # Rate limit статистика (примерный подсчет активных лимитов)
            rate_limit_pattern = "rate_limit:*"
            rate_limit_keys = await self.redis_client.keys(rate_limit_pattern)
            app_metrics["active_rate_limits"] = len(rate_limit_keys)
            
        except Exception as e:
            logger.error(f"Error collecting application metrics: {e}")
            
        return app_metrics
        
    def format_prometheus_metrics(self) -> str:
        """Форматирование метрик в формате Prometheus"""
        return generate_latest(self.registry)
        
    def get_metrics_json(self) -> Dict[str, Any]:
        """Получение метрик в JSON формате для дашборда"""
        try:
            text_metrics = self.format_prometheus_metrics()
            
            # Парсим основные метрики для JSON ответа
            metrics_data = {
                "timestamp": time.time(),
                "namespace": self.namespace,
                "system": {},
                "application": {},
                "llm": {}
            }
            
            # Простой парсинг Prometheus формата
            for line in text_metrics.split('\n'):
                if line and not line.startswith('#'):
                    try:
                        parts = line.split()
                        if len(parts) >= 2:
                            metric_name = parts[0]
                            value = float(parts[1])
                            
                            if 'system_' in metric_name:
                                metrics_data["system"][metric_name] = value
                            elif any(prefix in metric_name for prefix in ['telegram_', 'fsm_', 'crm_']):
                                metrics_data["application"][metric_name] = value
                            elif 'llm_' in metric_name:
                                metrics_data["llm"][metric_name] = value
                                
                    except (ValueError, IndexError):
                        continue
                        
            return metrics_data
            
        except Exception as e:
            logger.error(f"Error generating JSON metrics: {e}")
            return {
                "error": str(e),
                "timestamp": time.time()
            }


# Создаем alias для совместимости с существующим кодом
MetricsCollector = PrometheusMetricsCollector


class HealthChecker:
    """Проверщик состояния системы и компонентов"""
    
    def __init__(self, metrics_collector: PrometheusMetricsCollector):
        self.metrics_collector = metrics_collector
        self.components_to_check = [
            "redis",
            "telegram_bot", 
            "llm_orchestrator",
            "crm_adapter",
            "rate_limiter",
            "idempotency_manager",
            "sanitizer"
        ]
        
    async def check_redis_health(self) -> HealthCheck:
        """Проверка здоровья Redis"""
        start_time = time.time()
        
        try:
            await self.metrics_collector.redis_client.ping()
            response_time = (time.time() - start_time) * 1000
            
            # Проверка дополнительных показателей Redis
            info = await self.metrics_collector.redis_client.info()
            memory_usage = info.get('used_memory', 0)
            connected_clients = info.get('connected_clients', 0)
            
            return HealthCheck(
                component="redis",
                status="healthy",
                message="Redis is responsive",
                details={
                    "memory_usage_bytes": memory_usage,
                    "connected_clients": connected_clients,
                    "version": info.get('redis_version', 'unknown')
                },
                response_time_ms=response_time
            )
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return HealthCheck(
                component="redis",
                status="unhealthy",
                message=f"Redis connection failed: {str(e)}",
                response_time_ms=response_time
            )
            
    async def check_system_health(self) -> HealthCheck:
        """Проверка системных ресурсов"""
        try:
            # Проверка использования памяти
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            
            # Проверка использования диска
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            
            # Проверка загрузки CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            # Определение статуса на основе нагрузки
            if memory_percent > 90 or disk_percent > 90 or cpu_percent > 90:
                status = "unhealthy"
                message = "High system resource usage"
            elif memory_percent > 75 or disk_percent > 75 or cpu_percent > 75:
                status = "degraded"
                message = "Moderate system resource usage"
            else:
                status = "healthy"
                message = "System resources are normal"
                
            return HealthCheck(
                component="system",
                status=status,
                message=message,
                details={
                    "memory_percent": memory_percent,
                    "disk_percent": disk_percent,
                    "cpu_percent": cpu_percent,
                    "available_memory_gb": memory.available / (1024**3),
                    "free_disk_gb": disk.free / (1024**3)
                }
            )
        except Exception as e:
            return HealthCheck(
                component="system",
                status="unhealthy", 
                message=f"System check failed: {str(e)}"
            )
            
    async def check_component_health(self, component_name: str) -> HealthCheck:
        """Проверка здоровья конкретного компонента"""
        start_time = time.time()
        
        try:
            if component_name == "redis":
                return await self.check_redis_health()
            elif component_name == "system":
                return await self.check_system_health()
            else:
                # Для других компонентов пытаемся найти их health_check методы
                # Это упрощенная реализация - в реальности нужны ссылки на объекты
                return HealthCheck(
                    component=component_name,
                    status="unknown",
                    message=f"Health check not implemented for {component_name}",
                    response_time_ms=(time.time() - start_time) * 1000
                )
        except Exception as e:
            return HealthCheck(
                component=component_name,
                status="unhealthy",
                message=f"Health check failed: {str(e)}",
                response_time_ms=(time.time() - start_time) * 1000
            )
            
    async def run_full_health_check(self) -> Dict[str, Any]:
        """Полная проверка здоровья всех компонентов"""
        start_time = time.time()
        health_checks = {}
        
        # Проверка системы
        system_health = await self.check_system_health()
        health_checks["system"] = system_health
        
        # Проверка Redis
        redis_health = await self.check_redis_health()
        health_checks["redis"] = redis_health
        
        # Сбор системных метрик
        await self.metrics_collector.collect_system_metrics()
        app_metrics = await self.metrics_collector.collect_application_metrics()
        
        # Определение общего статуса
        all_statuses = [check.status for check in health_checks.values()]
        if "unhealthy" in all_statuses:
            overall_status = "unhealthy"
        elif "degraded" in all_statuses:
            overall_status = "degraded"
        else:
            overall_status = "healthy"
            
        total_time = time.time() - start_time
        
        return {
            "status": overall_status,
            "timestamp": time.time(),
            "total_check_time_ms": int(total_time * 1000),
            "components": {
                name: {
                    "status": check.status,
                    "message": check.message,
                    "details": check.details,
                    "response_time_ms": check.response_time_ms
                }
                for name, check in health_checks.items()
            },
            "application_metrics": app_metrics,
            "version": os.getenv('APP_VERSION', '1.0.0'),
            "environment": os.getenv('APP_ENV', 'production')
        }


# Глобальные экземпляры
_metrics_collector = None
_health_checker = None


async def get_metrics_collector(redis_url: str = None) -> PrometheusMetricsCollector:
    """Получение singleton экземпляра сборщика метрик"""
    global _metrics_collector
    
    if _metrics_collector is None:
        _metrics_collector = PrometheusMetricsCollector(redis_url)
        
    return _metrics_collector


async def get_health_checker() -> HealthChecker:
    """Получение экземпляра проверщика здоровья"""
    global _health_checker
    
    if _health_checker is None:
        collector = await get_metrics_collector()
        _health_checker = HealthChecker(collector)
        
    return _health_checker


# Декораторы для автоматического учета метрик
def track_duration(metric_name: str, labels: Optional[Dict[str, str]] = None):
    """Декоратор для трекинга времени выполнения функции с Prometheus histogram"""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                
                collector = await get_metrics_collector()
                collector.observe_histogram(metric_name, duration, labels)
                
                return result
            except Exception as e:
                duration = time.time() - start_time
                # Трекинг и ошибочных запросов
                collector = await get_metrics_collector()
                error_labels = dict(labels) if labels else {}
                error_labels['status'] = 'error'
                collector.observe_histogram(metric_name, duration, error_labels)
                raise e
                
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                
                # Для синхронных функций - используем asyncio.create_task если есть loop
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        async def update_metrics():
                            collector = await get_metrics_collector()
                            collector.observe_histogram(metric_name, duration, labels)
                        asyncio.create_task(update_metrics())
                except RuntimeError:
                    pass  # Нет активного loop
                    
                return result
            except Exception as e:
                duration = time.time() - start_time
                # Аналогично для ошибок
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        async def update_error_metrics():
                            collector = await get_metrics_collector()
                            error_labels = dict(labels) if labels else {}
                            error_labels['status'] = 'error'
                            collector.observe_histogram(metric_name, duration, error_labels)
                        asyncio.create_task(update_error_metrics())
                except RuntimeError:
                    pass
                raise e
                
        # Возвращаем правильный wrapper в зависимости от типа функции
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator


def count_calls(metric_name: str, labels: Optional[Dict[str, str]] = None):
    """Декоратор для подсчета вызовов функции"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            collector = await get_metrics_collector()
            
            try:
                result = await func(*args, **kwargs)
                collector.increment_counter(metric_name, labels=labels)
                return result
            except Exception as e:
                error_metric = metric_name.replace('_total', '_errors_total')
                collector.increment_counter(error_metric, labels=labels)
                raise
                
        return wrapper
    return decorator


__all__ = [
    'PrometheusMetricsCollector',
    'MetricsCollector',  # Alias for compatibility
    'HealthChecker',
    'HealthCheck',
    'MetricType',
    'get_metrics_collector',
    'get_health_checker',
    'track_duration',
    'count_calls'
]