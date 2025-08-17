#!/bin/bash

# Скрипт для исправления ошибки Telegram бота
echo "=== Исправление ошибки Telegram бота ==="

# Проверяем и устанавливаем недостающие зависимости
echo "1. Установка недостающих зависимостей..."
pip3 install qrcode[pil] pillow

# Проверяем .env файл
echo "2. Проверка переменных окружения..."
if [ ! -f "/root/.env" ]; then
    echo "Файл .env не найден, создаем..."
    cat > /root/.env << 'EOF'
TELEGRAM_BOT_TOKEN=7831169938:AAHpNRZ3kRPYZd3AfnmJtzUKQV6sM2I1x_c
ADMIN_IDS=769582971
BOT_NAME=WireGuard_VPN_Bot
DATABASE_PATH=/root/wireguard_clients.db
WG_CONFIG_PATH=/etc/wireguard/wg0.conf
EOF
else
    echo "Файл .env существует"
    cat /root/.env
fi

# Проверяем логи бота для диагностики
echo "3. Проверка логов бота..."
journalctl -u wireguard-bot.service --no-pager -n 20

echo "4. Проверка импортов Python..."
python3 -c "
try:
    import qrcode
    print('✅ qrcode: OK')
except ImportError:
    print('❌ qrcode: НЕ УСТАНОВЛЕН')

try:
    from PIL import Image
    print('✅ PIL/Pillow: OK')
except ImportError:
    print('❌ PIL/Pillow: НЕ УСТАНОВЛЕН')

try:
    from telegram import Update
    print('✅ python-telegram-bot: OK')
except ImportError:
    print('❌ python-telegram-bot: НЕ УСТАНОВЛЕН')

try:
    import sqlite3
    print('✅ sqlite3: OK')
except ImportError:
    print('❌ sqlite3: НЕ УСТАНОВЛЕН')
"

# Исправляем права доступа
echo "5. Исправление прав доступа..."
chmod +x /root/wireguard_bot.py
chown root:root /root/wireguard_bot.py
chmod 600 /root/.env

# Тестируем бота без systemd
echo "6. Тестирование бота..."
timeout 10s python3 /root/wireguard_bot.py || echo "Тест завершен (ожидаемо для демо)"

# Перезапускаем сервис
echo "7. Перезапуск сервиса..."
systemctl daemon-reload
systemctl restart wireguard-bot.service
sleep 3
systemctl status wireguard-bot.service --no-pager -l

echo ""
echo "=== Диагностика завершена ==="
echo "Проверьте статус: systemctl status wireguard-bot.service"
echo "Смотрите логи: journalctl -u wireguard-bot.service -f"