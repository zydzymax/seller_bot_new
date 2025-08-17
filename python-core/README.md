# SoVAni AI Seller Bot 🤖

Профессиональный AI-продавец трикотажа на основе GPT-5 с голосовыми возможностями и многоязычной поддержкой.

## 🚀 Основные возможности

### 💬 Интеллектуальные диалоги
- **GPT-5 интеграция** - Использует новейшую модель OpenAI GPT-5 с reasoning токенами
- **Контекстная память** - Запоминает историю диалогов и предпочтения клиентов
- **8-этапная воронка продаж** - Оптимизированный процесс от знакомства до закрытия сделки
- **Эмоциональный интеллект** - Распознавание настроения и адаптация стиля общения

### 🎙️ Голосовые возможности
- **Text-to-Speech** - Синтез речи через OpenAI TTS и ElevenLabs
- **Множество голосов** - Выбор из различных голосов (русские и английские)
- **Аудио-ответы в Telegram** - Отправка голосовых сообщений клиентам

### 🌐 Многоканальность
- **Telegram бот** - Основной канал взаимодействия с клиентами
- **Web API** - REST API для интеграции с внешними системами
- **Health Check** - Мониторинг состояния системы

### 📊 Аналитика и мониторинг
- **Детальное логирование** - Структурированные логи через structlog
- **Метрики производительности** - Отслеживание времени ответа и использования токенов
- **Circuit Breaker** - Защита от перегрузок и автоматическое восстановление

## 🏗️ Архитектура

### Ключевые компоненты
- **LLM Orchestrator** - Управление различными языковыми моделями
- **Provider Pattern** - Абстракция для работы с разными AI провайдерами
- **Adapter Pattern** - Адаптеры для OpenAI, ElevenLabs и других сервисов
- **Circuit Breaker** - Паттерн для отказоустойчивости

### Технологический стек
- **Python 3.10+** - Основной язык разработки
- **AsyncIO** - Асинхронное программирование
- **PostgreSQL** - Основная база данных
- **Redis** - Кэширование и сессии
- **Telegram Bot API** - Интеграция с Telegram
- **FastAPI** - Web API framework
- **Docker** - Контейнеризация

## 📁 Структура проекта

```
python-core/
├── adapters/           # Адаптеры для внешних сервисов
│   ├── openai_adapter.py       # GPT-5 интеграция
│   ├── elevenlabs_adapter.py   # Синтез речи
│   └── telegram_adapter.py     # Telegram API
├── llm/               # Система управления LLM
│   ├── orchestrator.py        # Основной оркестратор
│   ├── providers/             # Провайдеры AI моделей
│   └── circuit_breaker.py     # Отказоустойчивость
├── bot/               # Telegram бот
│   ├── telegram_bot.py        # Основной файл бота
│   ├── handlers/              # Обработчики сообщений
│   └── conversation/          # Логика диалогов
├── audio/             # Аудио функции
│   ├── text_to_speech.py      # TTS сервисы
│   └── voice_processor.py     # Обработка голоса
├── config/            # Конфигурация
│   ├── prompts.yaml           # Системные промпты
│   ├── settings.py            # Настройки приложения
│   └── database.py            # Конфигурация БД
├── utils/             # Утилиты
│   ├── circuit_breaker.py     # Паттерн circuit breaker
│   ├── logging_config.py      # Настройка логирования
│   └── memory_manager.py      # Управление памятью
└── tests/             # Тесты
    ├── unit/                  # Юнит тесты
    └── integration/           # Интеграционные тесты
```

## 🛠️ Установка и запуск

### Требования
- Python 3.10+
- PostgreSQL 12+
- Redis 6+
- OpenAI API ключ
- Telegram Bot Token

### Быстрый старт

1. **Клонирование репозитория**
```bash
git clone https://github.com/your-username/ai-seller-bot.git
cd ai-seller-bot
```

2. **Установка зависимостей**
```bash
pip install -r requirements.txt
```

3. **Настройка окружения**
```bash
cp .env.example .env
# Отредактируйте .env файл с вашими API ключами
```

4. **Инициализация базы данных**
```bash
python -m alembic upgrade head
```

5. **Запуск бота**
```bash
python bot/telegram_bot.py
```

### Переменные окружения

```bash
# API Keys
OPENAI_API_KEY=your_openai_api_key
TELEGRAM_TOKEN=your_telegram_bot_token
ELEVENLABS_API_KEY=your_elevenlabs_key  # Опционально

# База данных
POSTGRES_DSN=postgres://user:pass@localhost:5432/ai_seller
REDIS_URL=redis://127.0.0.1:6379/0

# Настройки приложения
APP_ENV=production
HEALTHCHECK_PORT=8082
```

## 🎯 Конфигурация GPT-5

Проект оптимизирован для работы с GPT-5:

- **Reasoning токены** - Автоматическое увеличение лимита токенов для reasoning
- **Температура** - Использует дефолтную температуру (GPT-5 не поддерживает кастомную)
- **max_completion_tokens** - Правильные параметры для GPT-5 API

```python
# Пример конфигурации
{
    "model": "gpt-5",
    "max_completion_tokens": 1500,  # Увеличено для reasoning токенов
    "messages": [...],
    # temperature не указывается (используется дефолтная)
}
```

## 🔊 Голосовые возможности

### Поддерживаемые TTS провайдеры
- **OpenAI TTS** - 6 голосов (alloy, echo, fable, onyx, nova, shimmer)
- **ElevenLabs** - Высококачественный синтез с кэшированием

### Настройка голосов
```python
# В config/prompts.yaml можно настроить предпочтительный голос
tts:
  default_voice: "nova"  # Для OpenAI
  fallback_voice: "alloy"
```

## 📈 Мониторинг и логи

### Структурированные логи
Все события логируются в структурированном формате:
```json
{
  "timestamp": "2025-08-16T14:30:15.123Z",
  "level": "info",
  "event": "message_processed",
  "user_id": 12345,
  "message_length": 45,
  "response_time_ms": 1234,
  "model": "gpt-5",
  "tokens_used": 150
}
```

### Health Check
```bash
curl http://localhost:8082/health
```

## 🔧 Конфигурация

### Промпты
Основные промпты находятся в `config/prompts.yaml`:
- Системный промпт для AI-продавца
- Промпты для разных этапов воронки продаж
- Эмоциональные промпты

### Настройки Circuit Breaker
```python
CIRCUIT_BREAKER_CONFIG = {
    "failure_threshold": 100,    # Количество ошибок для открытия
    "reset_timeout": 300,        # Время до попытки восстановления
    "expected_exception": Exception
}
```

## 🚦 Производственное развертывание

### SystemD сервис
```ini
[Unit]
Description=SoVAni AI Seller Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/ai_seller/project/python-core
Environment=OPENAI_API_KEY=your_key
Environment=TELEGRAM_TOKEN=your_token
ExecStart=/usr/bin/python3 bot/telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### VPN и безопасность
- Автоматическая маршрутизация OpenAI API через VPN
- Защита API ключей
- Логирование без раскрытия секретов

## 📊 Воронка продаж

8-этапная система:
1. **Приветствие** - Знакомство с клиентом
2. **Выявление потребностей** - Определение требований
3. **Презентация** - Показ подходящих товаров
4. **Работа с возражениями** - Ответы на вопросы
5. **Расчет стоимости** - Предложение цены
6. **Закрытие сделки** - Получение согласия
7. **Оформление заказа** - Сбор контактных данных
8. **Завершение** - Благодарность и дальнейшие шаги

## 🧪 Тестирование

```bash
# Запуск тестов
python -m pytest tests/

# Тесты с покрытием
python -m pytest tests/ --cov=.

# Интеграционные тесты
python -m pytest tests/integration/
```

## 📝 API Documentation

### REST API endpoints

- `GET /health` - Проверка состояния
- `POST /api/v1/message` - Отправка сообщения
- `GET /api/v1/stats` - Статистика использования

### Telegram команды

- `/start` - Начать диалог
- `/help` - Справка
- `/stats` - Статистика (для админов)
- `/voice on/off` - Включить/выключить голосовые ответы

## 🤝 Вклад в проект

1. Fork репозитория
2. Создайте feature branch (`git checkout -b feature/amazing-feature`)
3. Commit изменения (`git commit -m 'Add amazing feature'`)
4. Push в branch (`git push origin feature/amazing-feature`)
5. Создайте Pull Request

## 📄 Лицензия

© SoVAni 2025. Все права защищены.

## 📞 Поддержка

Для вопросов и поддержки обращайтесь к команде разработки.

---

**Версия:** 2.0.0  
**Последнее обновление:** 16 августа 2025  
**Статус:** Production Ready ✅