#!/bin/bash
# entrypoint.sh — Скрипт запуска AI-продавца с аудио поддержкой
# © SoVAni 2025

set -e

echo "🚀 Запуск AI-продавца SoVAni..."

# Проверяем переменные окружения
if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "❌ TELEGRAM_TOKEN не установлен"
    exit 1
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "⚠️ OPENAI_API_KEY не установлен - аудио функции будут недоступны"
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️ ANTHROPIC_API_KEY не установлен"
fi

# Создаем временные директории
mkdir -p /tmp/ai_seller_audio
chmod 755 /tmp/ai_seller_audio

echo "📁 Временная директория для аудио: /tmp/ai_seller_audio"

# Проверяем доступность аудио библиотек
echo "🔍 Проверка аудио зависимостей..."
python3 -c "import aiohttp, aiofiles; print('✅ Аудио зависимости доступны')" || {
    echo "❌ Ошибка: аудио зависимости недоступны"
    exit 1
}

# Очищаем старые аудио файлы при запуске
echo "🧹 Очистка старых аудио файлов..."
find /tmp/ai_seller_audio -name "*.mp3" -mtime +1 -delete 2>/dev/null || true
find /tmp/ai_seller_audio -name "*.ogg" -mtime +1 -delete 2>/dev/null || true

# Определяем режим запуска
MODE=${1:-"both"}

case $MODE in
    "go")
        echo "🟢 Запуск только Go API сервера..."
        exec /app/go-api-server
        ;;
    "python")
        echo "🐍 Запуск только Python Telegram бота..."
        cd /app/python-core
        exec python -m bot.telegram_bot
        ;;
    "both"|*)
        echo "🔄 Запуск обоих сервисов..."
        
        # Запускаем Go API в фоне
        echo "🟢 Запуск Go API сервера в фоне..."
        /app/go-api-server &
        GO_PID=$!
        
        # Ждем немного для запуска Go сервера
        sleep 3
        
        # Запускаем Python бота
        echo "🐍 Запуск Python Telegram бота..."
        cd /app/python-core
        python -m bot.telegram_bot &
        PYTHON_PID=$!
        
        # Функция для корректного завершения
        cleanup() {
            echo "🛑 Получен сигнал завершения..."
            echo "⏳ Завершение Go API сервера..."
            kill -TERM $GO_PID 2>/dev/null || true
            echo "⏳ Завершение Python бота..."
            kill -TERM $PYTHON_PID 2>/dev/null || true
            
            # Ждем завершения процессов
            wait $GO_PID 2>/dev/null || true
            wait $PYTHON_PID 2>/dev/null || true
            
            echo "✅ Все процессы завершены."
            exit 0
        }
        
        # Устанавливаем обработчик сигналов
        trap cleanup SIGTERM SIGINT
        
        echo "✅ Все сервисы запущены. PID Go: $GO_PID, PID Python: $PYTHON_PID"
        echo "🎯 Ожидание завершения..."
        
        # Ждем завершения любого из процессов
        wait -n
        
        # Если один из процессов завершился, завершаем все
        cleanup
        ;;
esac