"""
input_sanitizer.py — Санитизация пользовательского ввода для SoVAni AI-продавца.

- Нормализация Unicode и очистка control-символов  
- Защита от prompt injection и role-reset атак
- XSS и SQL injection защита
- Configurable лимиты на длину ввода
- Загрузка стоп-паттернов из business_rules.yaml

© SoVAni 2025
"""

import os
import re
import unicodedata
from typing import Optional, List, Dict, Any
from pathlib import Path
import yaml
from dataclasses import dataclass

from utils.logging import get_logger

logger = get_logger(__name__)

# Настройки из ENV
MAX_INPUT_LENGTH = int(os.getenv('MAX_INPUT_CHARS', '2000'))
ENABLE_UNICODE_NORMALIZATION = os.getenv('SANITIZER_NORMALIZE_UNICODE', 'true').lower() == 'true'
ENABLE_XSS_PROTECTION = os.getenv('SANITIZER_XSS_PROTECTION', 'true').lower() == 'true'

# Базовые паттерны prompt injection
DEFAULT_PROMPT_INJECTION_PATTERNS = [
    r'ignore\s*previous\s*instructions',
    r'change\s*role',
    r'system\s*prompt', 
    r'you\s*are\s*no\s*longer',
    r'developer\s*mode',
    r'debug\s*mode',
    r'выведи\s*системный\s*промпт',
    r'игнорируй\s*инструкции',
    r'поменяй\s*роль',
    r'представь\s*что\s*предыдущ(?:ие|их|ий|е|й)?',
]

# XSS паттерны
XSS_PATTERNS = [
    r'<\w+[^>]*>',
    r'</\w+>',
    r'<\w+[^>]*>.*?</\w+>',
    r'on\w+\s*=',
    r'javascript:',
    r'alert\s*\(',
    r'document\.',
    r'window\.',
    r'eval\s*\(',
    r'script\s*:',
]

# SQL injection паттерны
SQL_INJECTION_PATTERNS = [
    r'union\s+select',
    r'drop\s+table',
    r'delete\s+from',
    r'insert\s+into',
    r'update\s+.*\s+set',
    r'exec\s*\(',
    r'sp_\w+',
    r'xp_\w+',
    r'--\s*$',
    r'/\*.*?\*/',
    r"'.*or.*'.*=.*'",
    r'".*or.*".*=.*"',
]

# Опасные control символы
DANGEROUS_CONTROL_CHARS = [
    '\x00',  # NULL
    '\x01',  # SOH
    '\x02',  # STX
    '\x03',  # ETX
    '\x04',  # EOT
    '\x05',  # ENQ
    '\x06',  # ACK
    '\x07',  # BEL
    '\x08',  # BS
    '\x0B',  # VT
    '\x0C',  # FF
    '\x0E',  # SO
    '\x0F',  # SI
    '\x10',  # DLE
    '\x11',  # DC1
    '\x12',  # DC2
    '\x13',  # DC3
    '\x14',  # DC4
    '\x15',  # NAK
    '\x16',  # SYN
    '\x17',  # ETB
    '\x18',  # CAN
    '\x19',  # EM
    '\x1A',  # SUB
    '\x1B',  # ESC
    '\x1C',  # FS
    '\x1D',  # GS
    '\x1E',  # RS
    '\x1F',  # US
    '\x7F',  # DEL
]


@dataclass
class SanitizationResult:
    """Результат санитизации ввода"""
    sanitized_text: str
    was_modified: bool = False
    violations: List[str] = None
    warning_flags: List[str] = None
    
    def __post_init__(self):
        if self.violations is None:
            self.violations = []
        if self.warning_flags is None:
            self.warning_flags = []


class InputSanitizer:
    """Комплексный санитайзер пользовательского ввода"""
    
    def __init__(self, business_rules_path: Optional[str] = None):
        self.business_rules_path = business_rules_path or self._get_default_rules_path()
        self.business_rules = self._load_business_rules()
        
        # Компиляция регулярных выражений для производительности
        self._compile_patterns()
        
    def _get_default_rules_path(self) -> str:
        """Получение пути к файлу бизнес-правил по умолчанию"""
        return str(Path(__file__).parent.parent / "config" / "business_rules.yaml")
        
    def _load_business_rules(self) -> Dict[str, Any]:
        """Загрузка бизнес-правил из YAML файла"""
        try:
            with open(self.business_rules_path, 'r', encoding='utf-8') as f:
                rules = yaml.safe_load(f)
                logger.info("Business rules loaded successfully")
                return rules
        except Exception as e:
            logger.warning(f"Failed to load business rules: {e}. Using defaults.")
            return {}
            
    def _compile_patterns(self):
        """Компиляция регулярных выражений"""
        # Паттерны из бизнес-правил
        custom_patterns = self.business_rules.get('guards', {}).get('role_reset_patterns', [])
        all_prompt_patterns = DEFAULT_PROMPT_INJECTION_PATTERNS + custom_patterns
        
        self.prompt_injection_patterns = [
            re.compile(pattern, re.IGNORECASE | re.MULTILINE) 
            for pattern in all_prompt_patterns
        ]
        
        if ENABLE_XSS_PROTECTION:
            self.xss_patterns = [
                re.compile(pattern, re.IGNORECASE | re.MULTILINE)
                for pattern in XSS_PATTERNS
            ]
        else:
            self.xss_patterns = []
            
        self.sql_patterns = [
            re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            for pattern in SQL_INJECTION_PATTERNS
        ]
        
    def normalize_unicode(self, text: str) -> str:
        """Нормализация Unicode строк"""
        if not ENABLE_UNICODE_NORMALIZATION:
            return text
            
        # NFKC нормализация для преобразования визуально похожих символов
        normalized = unicodedata.normalize('NFKC', text)
        
        # Удаление комбинирующих символов которые могут маскировать атаки
        cleaned = ''.join(
            char for char in normalized 
            if not unicodedata.combining(char) or unicodedata.category(char) in ['Mn', 'Mc']
        )
        
        return cleaned
        
    def remove_control_chars(self, text: str) -> str:
        """Удаление опасных control символов"""
        # Сохраняем полезные символы: \n, \r, \t
        cleaned = text
        for char in DANGEROUS_CONTROL_CHARS:
            cleaned = cleaned.replace(char, '')
            
        # Дополнительная очистка через Unicode категории
        cleaned = ''.join(
            char for char in cleaned 
            if unicodedata.category(char) not in ['Cc', 'Cf', 'Co'] or char in ['\n', '\r', '\t']
        )
        
        return cleaned
        
    def check_length_limits(self, text: str) -> tuple[str, List[str]]:
        """Проверка и обрезка по лимитам длины"""
        violations = []
        
        if len(text) > MAX_INPUT_LENGTH:
            violations.append(f"input_too_long:{len(text)}")
            text = text[:MAX_INPUT_LENGTH]
            logger.warning(f"Input truncated to {MAX_INPUT_LENGTH} characters")
            
        return text, violations
        
    def detect_prompt_injection(self, text: str) -> List[str]:
        """Обнаружение попыток prompt injection"""
        violations = []
        
        for pattern in self.prompt_injection_patterns:
            if pattern.search(text):
                violations.append(f"prompt_injection:{pattern.pattern}")
                logger.warning(f"Prompt injection detected: {pattern.pattern}")
                
        return violations
        
    def detect_xss(self, text: str) -> List[str]:
        """Обнаружение XSS атак"""
        violations = []
        
        for pattern in self.xss_patterns:
            if pattern.search(text):
                violations.append(f"xss_attempt:{pattern.pattern}")
                logger.warning(f"XSS attempt detected: {pattern.pattern}")
                
        return violations
        
    def detect_sql_injection(self, text: str) -> List[str]:
        """Обнаружение SQL injection"""
        violations = []
        
        for pattern in self.sql_patterns:
            if pattern.search(text):
                violations.append(f"sql_injection:{pattern.pattern}")
                logger.warning(f"SQL injection detected: {pattern.pattern}")
                
        return violations
        
    def clean_html_entities(self, text: str) -> str:
        """Очистка HTML entities и декодирование"""
        import html
        
        # Декодирование HTML entities
        decoded = html.unescape(text)
        
        # Удаление потенциально опасных HTML тегов
        # Простая очистка - удаляем всё что похоже на теги
        cleaned = re.sub(r'<[^>]+>', '', decoded)
        
        return cleaned
        
    def sanitize(self, text: str) -> SanitizationResult:
        """
        Комплексная санитизация входного текста
        
        Args:
            text: Исходный текст для санитизации
            
        Returns:
            SanitizationResult с результатами санитизации
        """
        if not text or not isinstance(text, str):
            return SanitizationResult(
                sanitized_text="",
                was_modified=False
            )
            
        original_text = text
        violations = []
        warnings = []
        
        try:
            # 1. Нормализация Unicode
            text = self.normalize_unicode(text)
            
            # 2. Удаление control символов
            text = self.remove_control_chars(text)
            
            # 3. Очистка HTML entities
            text = self.clean_html_entities(text)
            
            # 4. Проверка лимитов длины
            text, length_violations = self.check_length_limits(text)
            violations.extend(length_violations)
            
            # 5. Обнаружение угроз безопасности
            prompt_violations = self.detect_prompt_injection(text)
            violations.extend(prompt_violations)
            
            xss_violations = self.detect_xss(text)
            violations.extend(xss_violations)
            
            sql_violations = self.detect_sql_injection(text)
            violations.extend(sql_violations)
            
            # 6. Дополнительные проверки
            # Проверка на чрезмерное количество повторяющихся символов
            if re.search(r'(.)\1{50,}', text):
                warnings.append("excessive_repetition")
                text = re.sub(r'(.)\1{50,}', r'\1' * 50, text)
                
            # Проверка на подозрительные Unicode блоки
            suspicious_blocks = ['MISCELLANEOUS_SYMBOLS', 'DINGBATS', 'PRIVATE_USE_AREA']
            for char in text:
                try:
                    block = unicodedata.name(char, '').split()[0] if unicodedata.name(char, '') else ''
                    if any(suspect in block for suspect in suspicious_blocks):
                        warnings.append(f"suspicious_unicode:{block}")
                        break
                except:
                    pass
                    
            # 7. Финальная очистка пробелов
            text = ' '.join(text.split())  # Нормализация пробелов
            text = text.strip()
            
            was_modified = (text != original_text)
            
            if was_modified:
                logger.info("Text was sanitized", 
                           original_length=len(original_text),
                           sanitized_length=len(text),
                           violations=len(violations),
                           warnings=len(warnings))
                           
            return SanitizationResult(
                sanitized_text=text,
                was_modified=was_modified,
                violations=violations,
                warning_flags=warnings
            )
            
        except Exception as e:
            logger.error(f"Sanitization error: {e}")
            # В случае ошибки возвращаем максимально безопасный результат
            safe_text = re.sub(r'[^\w\s\-.,!?]', '', original_text)[:MAX_INPUT_LENGTH]
            return SanitizationResult(
                sanitized_text=safe_text,
                was_modified=True,
                violations=[f"sanitization_error:{str(e)}"],
                warning_flags=["emergency_cleanup"]
            )
            
    def is_safe(self, text: str) -> bool:
        """
        Быстрая проверка безопасности текста
        
        Args:
            text: Текст для проверки
            
        Returns:
            True если текст безопасен, False если содержит угрозы
        """
        result = self.sanitize(text)
        return len(result.violations) == 0
        
    def health_check(self) -> Dict[str, Any]:
        """Проверка состояния санитайзера"""
        try:
            # Тестовая санитизация
            test_input = "Hello <script>alert('test')</script> World"
            result = self.sanitize(test_input)
            
            return {
                "status": "healthy",
                "business_rules_loaded": bool(self.business_rules),
                "patterns_compiled": {
                    "prompt_injection": len(self.prompt_injection_patterns),
                    "xss": len(self.xss_patterns),
                    "sql": len(self.sql_patterns)
                },
                "config": {
                    "max_input_length": MAX_INPUT_LENGTH,
                    "unicode_normalization": ENABLE_UNICODE_NORMALIZATION,
                    "xss_protection": ENABLE_XSS_PROTECTION
                },
                "test_sanitization_worked": result.was_modified
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }


# Глобальный экземпляр санитайзера
_sanitizer_instance = None


def get_sanitizer() -> InputSanitizer:
    """Получение singleton экземпляра санитайзера"""
    global _sanitizer_instance
    
    if _sanitizer_instance is None:
        _sanitizer_instance = InputSanitizer()
        
    return _sanitizer_instance


def sanitize_input(text: str) -> SanitizationResult:
    """
    Быстрая функция для санитизации ввода
    
    Args:
        text: Текст для санитизации
        
    Returns:
        SanitizationResult с результатами санитизации
    """
    sanitizer = get_sanitizer()
    return sanitizer.sanitize(text)


def is_input_safe(text: str) -> bool:
    """
    Быстрая проверка безопасности ввода
    
    Args:
        text: Текст для проверки
        
    Returns:
        True если безопасен, False если содержит угрозы
    """
    sanitizer = get_sanitizer()
    return sanitizer.is_safe(text)


__all__ = [
    'InputSanitizer',
    'SanitizationResult', 
    'sanitize_input',
    'is_input_safe',
    'get_sanitizer'
]