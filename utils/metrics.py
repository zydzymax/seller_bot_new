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
from utils.logging import get_logger

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


class MetricsCollector:
    """Сборщик метрик для SoVAni AI seller"""
    
    def __init__(self, redis_url: str = None):
        self.redis_client = redis.from_url(redis_url or os.getenv('REDIS_ADDR', 'redis://localhost:6379/0'))
        self.metrics: Dict[str, Metric] = {}
        self.app_start_time = time.time()
        self.namespace = "sovani_ai_seller"
        
        # Инициализация базовых метрик
        self._init_base_metrics()
        
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
            
    def increment_counter(
        self, 
        metric_name: str, 
        value: float = 1.0, 
        labels: Optional[Dict[str, str]] = None
    ):
        """Увеличение счетчика"""
        full_name = f"{self.namespace}_{metric_name}"
        if full_name in self.metrics:
            self.metrics[full_name].value += value
            if labels:
                self.metrics[full_name].labels.update(labels)
            self.metrics[full_name].timestamp = time.time()
            
        logger.debug(f"Counter {metric_name} incremented by {value}", labels=labels)
        
    def set_gauge(
        self, 
        metric_name: str, 
        value: float, 
        labels: Optional[Dict[str, str]] = None
    ):
        """Установка значения gauge метрики"""
        full_name = f"{self.namespace}_{metric_name}"
        if full_name in self.metrics:
            self.metrics[full_name].value = value
            if labels:
                self.metrics[full_name].labels.update(labels)
            self.metrics[full_name].timestamp = time.time()
            
        logger.debug(f"Gauge {metric_name} set to {value}", labels=labels)
        
    def record_duration(
        self, 
        metric_name: str, 
        duration_seconds: float,
        labels: Optional[Dict[str, str]] = None
    ):
        """Запись времени выполнения для histogram метрики"""
        full_name = f"{self.namespace}_{metric_name}"
        if full_name in self.metrics:
            # Для простоты сохраняем последнее значение
            # В реальной реализации здесь был бы histogram с bucket'ами
            self.metrics[full_name].value = duration_seconds
            if labels:
                self.metrics[full_name].labels.update(labels)
            self.metrics[full_name].timestamp = time.time()
            
        # Также увеличиваем общий счетчик операций
        base_name = metric_name.replace('_duration_seconds', '_total')
        self.increment_counter(base_name, labels=labels)
        
        logger.debug(f"Duration {metric_name} recorded: {duration_seconds:.3f}s", labels=labels)
        
    async def collect_system_metrics(self):
        """Сбор системных метрик"""
        try:
            # Использование памяти
            memory_info = psutil.virtual_memory()
            self.set_gauge("system_memory_usage_bytes", memory_info.used)
            
            # Использование CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self.set_gauge("system_cpu_usage_percent", cpu_percent)
            
            # Uptime приложения
            uptime = time.time() - self.app_start_time
            self.set_gauge("app_uptime_seconds", uptime)
            
            # Статус Redis
            try:
                await self.redis_client.ping()
                self.set_gauge("redis_connected", 1)
            except:
                self.set_gauge("redis_connected", 0)
                
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
        lines = []
        
        for metric in self.metrics.values():
            # HELP строка
            lines.append(f"# HELP {metric.name} {metric.description}")
            # TYPE строка
            lines.append(f"# TYPE {metric.name} {metric.type.value}")
            
            # Значение метрики с лейблами
            if metric.labels:
                label_pairs = [f'{k}="{v}"' for k, v in metric.labels.items()]
                label_str = "{" + ",".join(label_pairs) + "}"
            else:
                label_str = ""
                
            lines.append(f"{metric.name}{label_str} {metric.value}")
            lines.append("")  # Пустая строка между метриками
            
        return "\n".join(lines)
        
    def get_metrics_json(self) -> Dict[str, Any]:
        """Получение метрик в JSON формате"""
        return {
            "timestamp": time.time(),
            "namespace": self.namespace,
            "metrics": {
                name: {
                    "value": metric.value,
                    "type": metric.type.value,
                    "description": metric.description,
                    "labels": metric.labels,
                    "timestamp": metric.timestamp
                }
                for name, metric in self.metrics.items()
            }
        }


class HealthChecker:
    """Проверщик состояния системы и компонентов"""
    
    def __init__(self, metrics_collector: MetricsCollector):
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


async def get_metrics_collector(redis_url: str = None) -> MetricsCollector:
    """Получение singleton экземпляра сборщика метрик"""
    global _metrics_collector
    
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector(redis_url)
        
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
    """Декоратор для отслеживания времени выполнения функции"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            collector = await get_metrics_collector()
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                collector.record_duration(metric_name, duration, labels)
                return result
            except Exception as e:
                duration = time.time() - start_time
                collector.record_duration(metric_name, duration, labels)
                
                # Увеличиваем счетчик ошибок
                error_metric = metric_name.replace('_duration_seconds', '_errors_total')
                collector.increment_counter(error_metric, labels=labels)
                raise
                
        return wrapper
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
    'MetricsCollector',
    'HealthChecker',
    'HealthCheck',
    'MetricType',
    'get_metrics_collector',
    'get_health_checker',
    'track_duration',
    'count_calls'
]