"""
logging.py — Централизованная система логирования для SoVAni AI-продавца.

- Структурированные логи в JSON формате
- Контекстная информация (trace_id, chat_id)
- Безопасность: исключение PII данных
- Ротация логов и архивирование

© SoVAni 2025
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path

import structlog
from structlog.contextvars import clear_contextvars, bind_contextvars
import orjson


class PIIFilter:
    """Фильтр для удаления персональных данных из логов"""
    
    PII_FIELDS = {
        'phone', 'email', 'telegram_id', 'user_id', 'chat_id',
        'first_name', 'last_name', 'username', 'contact',
        'address', 'inn', 'passport'
    }
    
    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Санитизация словаря от PII"""
        if not isinstance(data, dict):
            return data
            
        sanitized = {}
        for key, value in data.items():
            if key.lower() in cls.PII_FIELDS:
                # Маскировка чувствительных данных
                if isinstance(value, str) and value:
                    if '@' in value:  # Email
                        sanitized[key] = f"{value[:2]}***@{value.split('@')[1]}"
                    elif value.isdigit():  # Phone/ID
                        sanitized[key] = f"{value[:3]}***{value[-2:]}" if len(value) > 5 else "***"
                    else:  # Other text
                        sanitized[key] = f"{value[:2]}***"
                else:
                    sanitized[key] = "***"
            elif isinstance(value, dict):
                sanitized[key] = cls.sanitize_dict(value)
            elif isinstance(value, list):
                sanitized[key] = [cls.sanitize_dict(item) if isinstance(item, dict) else item for item in value]
            else:
                sanitized[key] = value
                
        return sanitized


class JSONFormatter(logging.Formatter):
    """JSON форматтер для структурированных логов"""
    
    def format(self, record: logging.LogRecord) -> str:
        # Базовая информация
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Добавление контекста из structlog
        if hasattr(record, '_record'):
            context = getattr(record._record, 'context', {})
            if context:
                # Санитизация контекста от PII
                sanitized_context = PIIFilter.sanitize_dict(context)
                log_data.update(sanitized_context)
        
        # Добавление extra данных
        for key, value in record.__dict__.items():
            if key not in {'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 
                          'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
                          'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
                          'thread', 'threadName', 'processName', 'process', 'getMessage',
                          '_record'}:
                # Санитизация дополнительных данных
                if isinstance(value, dict):
                    log_data[key] = PIIFilter.sanitize_dict(value)
                else:
                    log_data[key] = value
        
        # Добавление информации об исключении
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info)
            }
            
        # Используем orjson для быстрой сериализации
        return orjson.dumps(
            log_data, 
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY
        ).decode('utf-8')


class PerformanceLogFilter(logging.Filter):
    """Фильтр для метрик производительности"""
    
    def filter(self, record):
        # Помечаем performance метрики
        if hasattr(record, 'duration_ms') or 'processing_time' in record.getMessage().lower():
            record.category = 'performance'
        return True


def configure_logging(
    level: str = None,
    log_file: Optional[str] = None,
    enable_console: bool = True,
    json_format: bool = True,
    enable_performance_logging: bool = True
):
    """
    Конфигурация системы логирования
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Путь к файлу логов (опционально)
        enable_console: Включить вывод в консоль
        json_format: Использовать JSON формат (иначе обычный текст)
    """
    # Получение уровня из ENV или использование переданного
    log_level = level or os.getenv('LOG_LEVEL', 'INFO').upper()
    
    # Очистка существующих логгеров
    logging.root.handlers.clear()
    
    handlers = []
    
    # Консольный обработчик
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        if json_format:
            console_handler.setFormatter(JSONFormatter())
        else:
            console_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_handler.setFormatter(console_formatter)
        handlers.append(console_handler)
    
    # Файловый обработчик
    if log_file:
        # Создание директории для логов если нужно
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        if json_format:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)
    
    # Конфигурация root logger
    logging.basicConfig(
        level=getattr(logging, log_level),
        handlers=handlers,
        force=True
    )
    
    # Конфигурация structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level)),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """
    Получение логгера с заданным именем
    
    Args:
        name: Имя логгера (обычно __name__)
        
    Returns:
        Настроенный structlog логгер
    """
    return structlog.get_logger(name)


def log_performance(operation: str, duration_ms: int, **extra_data):
    """
    Логирование метрик производительности
    
    Args:
        operation: Название операции
        duration_ms: Продолжительность в мс
        **extra_data: Дополнительные метрики
    """
    logger = get_logger('performance')
    logger.info(
        f"Performance: {operation} completed",
        operation=operation,
        duration_ms=duration_ms,
        category='performance',
        **extra_data
    )
    

def set_context(**kwargs):
    """
    Установка контекста для текущего запроса
    
    Args:
        **kwargs: Контекстные данные (trace_id, chat_id, user_id и т.д.)
    """
    # Санитизация контекста от PII
    sanitized_context = PIIFilter.sanitize_dict(kwargs)
    bind_contextvars(**sanitized_context)


def clear_context():
    """Очистка контекста"""
    clear_contextvars()


def with_context(**context_data):
    """
    Декоратор для добавления контекста к функции
    
    Args:
        **context_data: Контекстные данные
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Сохранение текущего контекста
            original_context = structlog.contextvars.get_contextvars()
            
            try:
                # Установка нового контекста
                set_context(**context_data)
                return func(*args, **kwargs)
            finally:
                # Восстановление оригинального контекста
                clear_context()
                if original_context:
                    bind_contextvars(**original_context)
                    
        return wrapper
    return decorator


# Автоматическая конфигурация при импорте модуля
if not logging.root.handlers:
    # Настройки по умолчанию из ENV
    configure_logging(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        log_file=os.getenv('LOG_FILE'),
        enable_console=os.getenv('LOG_CONSOLE', 'true').lower() == 'true',
        json_format=os.getenv('LOG_JSON', 'true').lower() == 'true'
    )


# Экспорт основных функций
__all__ = [
    'get_logger',
    'set_context', 
    'clear_context',
    'with_context',
    'configure_logging',
    'log_performance',
    'PIIFilter',
    'JSONFormatter',
    'PerformanceLogFilter'
]