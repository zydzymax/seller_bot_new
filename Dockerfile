# SoVAni AI-продавец Docker образ (Python Core)
# © SoVAni 2025

FROM python:3.11-slim

# Метаданные
LABEL maintainer="SoVAni Team"
LABEL version="1.0.0"
LABEL description="SoVAni AI Seller Python Core Application"

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libpq-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Создание пользователя приложения
RUN groupadd -r sovani && useradd -r -g sovani sovani

# Рабочая директория
WORKDIR /app

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание необходимых директорий
RUN mkdir -p /app/logs /app/temp && \
    chown -R sovani:sovani /app

# Переключение на пользователя приложения
# USER sovani

# Переменные окружения
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Проверка здоровья
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Порт приложения
EXPOSE 8000

# Запуск приложения
CMD ["python", "-m", "uvicorn", "bot.webhook:app", "--host", "0.0.0.0", "--port", "8000"]