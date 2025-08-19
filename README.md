# SoVAni AI Seller - Python Core

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-7+-red.svg)](https://redis.io/)
[![License](https://img.shields.io/badge/license-SoVAni-orange.svg)](LICENSE)

> Профессиональный AI-продавец для текстильного производства с жёсткими бизнес-правилами и FSM на Redis.

## 🎯 Основные возможности

### 🤖 AI Оркестратор
- **Умная маршрутизация** запросов между LLM провайдерами
- **Fallback механизмы** при сбоях API
- **Кэширование** ответов с TTL
- **Rate limiting** и retry логика
- **Метрики** использования и производительности

### 🔄 Finite State Machine (FSM)
- **Жёсткие бизнес-правила** для текстильного производства
- **MOQ валидация** (минимум 1000 шт/цвет для давальческой схемы)
- **Автоматические переходы** между состояниями диалога
- **Персистентность** состояний в Redis
- **Контекст диалога** с полной историей заказа

### 🛡️ Безопасность и надёжность
- **Input sanitization** с защитой от prompt injection и XSS
- **Rate limiting** по chat_id с sliding window
- **Идемпотентность** webhook'ов по update_id
- **DLQ** (Dead Letter Queue) для неуспешных CRM запросов
- **Health checks** и мониторинг состояния

### 📊 Мониторинг и метрики
- **Prometheus метрики** для всех компонентов
- **Structured logging** с контекстом
- **Health checks** с детальной диагностикой
- **Performance tracking** для LLM и CRM запросов

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
git clone <repository>
cd python-core

# Копирование и настройка переменных окружения
cp .env.example .env
# Отредактируйте .env файл с вашими API ключами
```

### 2. Запуск через Docker Compose

```bash
# Запуск всех сервисов
docker-compose up -d

# Проверка состояния
docker-compose ps

# Просмотр логов
docker-compose logs -f ai_seller
```

### 3. Проверка работоспособности

```bash
# Health check
curl http://localhost:8000/healthz

# Readiness check с детальной диагностикой
curl http://localhost:8000/readyz

# Prometheus метрики
curl http://localhost:8000/metrics
```

## ⚙️ Конфигурация

### Основные переменные окружения

```bash
# API ключи
TELEGRAM_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_key

# Redis для FSM и кэширования
REDIS_ADDR=redis://localhost:6379/0

# Webhook настройки
WEBHOOK_SECRET_PATH=your_secret_path

# CRM интеграция
AMOCRM_DOMAIN=your-domain.amocrm.ru
AMOCRM_API_TOKEN=your_token
```

## 🧪 Тестирование

```bash
# Установка зависимостей
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov

# Запуск тестов
pytest

# Запуск с покрытием кода
pytest --cov=. --cov-report=html
```

## 📊 Мониторинг

### Prometheus метрики (`/metrics`)
- `sovani_ai_seller_telegram_updates_total` - Количество обработанных updates
- `sovani_ai_seller_llm_requests_total` - Запросы к LLM
- `sovani_ai_seller_crm_leads_created_total` - Созданные лиды в CRM

### Health Checks
- **`/healthz`** - Простая проверка доступности
- **`/readyz`** - Детальная проверка всех компонентов

## 🤝 FSM Состояния

1. **GREETING** - Приветствие
2. **PRODUCT_INQUIRY** - Выяснение типа продукта
3. **QUANTITY_COLORS** - Количество и цвета
4. **MOQ_VALIDATION** - Валидация минимального заказа
5. **FABRIC_DETAILS** - Детали ткани
6. **PRICING_MODE** - Ценообразование
7. **CONTACT_COLLECTION** - Сбор контактов
8. **FINALIZATION** - Финализация
9. **COMPLETED** - Отправка в CRM

## 📄 Лицензия

© SoVAni 2025. Все права защищены.