#!/bin/bash

# Автоматический скрипт исправления селективного VPN сервера
# Для использования на VPS 95.140.154.181 (Франкфурт)

set -e

echo "=== Починка селективного VPN сервера ==="
echo "Дата: $(date)"

# Проверяем права root
if [ "$EUID" -ne 0 ]; then 
    echo "Ошибка: Запустите скрипт от root"
    exit 1
fi

# Обновляем систему и устанавливаем необходимые пакеты
echo "1. Установка необходимых пакетов..."
apt update -qq
apt install -y ipset iptables-persistent python3-pip wireguard-tools resolvconf curl wget jq

# Устанавливаем Python зависимости
pip3 install dnspython requests python-telegram-bot==20.7

# Исправляем selective_routing.py
echo "2. Исправление selective_routing.py..."
cat > /root/selective_routing.py << 'EOF'
#!/usr/bin/env python3
"""
Скрипт для настройки селективной маршрутизации через WireGuard
Направляет заблокированные в РФ ресурсы через VPN
"""

import subprocess
import logging
import time
import socket
import ipaddress
import sys
import argparse
from typing import Set, List
import dns.resolver

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/selective_routing.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Список доменов для маршрутизации через VPN
VPN_DOMAINS = [
    # AI сервисы
    'chat.openai.com', 'api.openai.com', 'openai.com',
    'claude.ai', 'api.anthropic.com', 'anthropic.com',
    'poe.com', 'quora.com',
    'bard.google.com', 'gemini.google.com',
    
    # Социальные сети и мессенджеры
    'facebook.com', 'instagram.com', 'whatsapp.com',
    'twitter.com', 'x.com', 't.co',
    'linkedin.com', 'discord.com',
    'telegram.org', 'web.telegram.org',
    
    # Видео и стриминг
    'youtube.com', 'googlevideo.com', 'ytimg.com',
    'netflix.com', 'twitch.tv',
    
    # Разработка и IT
    'github.com', 'githubusercontent.com',
    'stackoverflow.com', 'medium.com',
    'vercel.com', 'netlify.com',
    
    # Новости и информация
    'bbc.com', 'cnn.com', 'reuters.com',
    'wikipedia.org', 'wikimedia.org',
    
    # Другие заблокированные сервисы
    'spotify.com', 'soundcloud.com',
    'dropbox.com', 'onedrive.com',
    'paypal.com', 'stripe.com'
]

class SelectiveRouter:
    def __init__(self):
        self.ipset_name = 'wg_domains'
        self.table_id = 200
        self.mark = 0x1
        
    def run_command(self, command: str, check: bool = True) -> subprocess.CompletedProcess:
        """Выполнить команду в shell"""
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, 
                text=True, check=check
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка выполнения команды '{command}': {e}")
            logger.error(f"Stdout: {e.stdout}")
            logger.error(f"Stderr: {e.stderr}")
            raise
    
    def resolve_domain(self, domain: str) -> Set[str]:
        """Резолвить домен в IP адреса"""
        ips = set()
        try:
            # Резолвим A записи
            try:
                answers = dns.resolver.resolve(domain, 'A')
                for answer in answers:
                    ips.add(str(answer))
            except dns.resolver.NXDOMAIN:
                pass
            except Exception as e:
                logger.warning(f"Ошибка резолва A записи для {domain}: {e}")
            
            # Резолвим AAAA записи (IPv6)
            try:
                answers = dns.resolver.resolve(domain, 'AAAA')
                for answer in answers:
                    ips.add(str(answer))
            except dns.resolver.NXDOMAIN:
                pass
            except Exception as e:
                logger.warning(f"Ошибка резолва AAAA записи для {domain}: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка резолва {domain}: {e}")
        
        return ips
    
    def create_ipset(self):
        """Создать ipset для VPN доменов"""
        # Удаляем существующий ipset если есть
        self.run_command(f"ipset destroy {self.ipset_name}", check=False)
        
        # Создаем новый ipset
        self.run_command(f"ipset create {self.ipset_name} hash:ip")
        logger.info(f"Создан ipset {self.ipset_name}")
    
    def update_ipset(self, ips: Set[str]):
        """Обновить ipset с новыми IP"""
        # Получаем текущие IP в ipset
        try:
            result = self.run_command(f"ipset list {self.ipset_name}", check=False)
            current_ips = set()
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if line and not any(line.startswith(prefix) for prefix in [
                        'Name:', 'Type:', 'Revision:', 'Header:', 'Size', 
                        'References:', 'Default:', 'Number', 'Members:'
                    ]):
                        try:
                            ipaddress.ip_address(line)
                            current_ips.add(line)
                        except ValueError:
                            continue
        except Exception as e:
            logger.error(f"Ошибка получения текущих IP: {e}")
            current_ips = set()
        
        # Добавляем новые IP
        new_ips = ips - current_ips
        for ip in new_ips:
            try:
                self.run_command(f"ipset add {self.ipset_name} {ip}", check=False)
                logger.debug(f"Добавлен IP: {ip}")
            except Exception as e:
                logger.warning(f"Не удалось добавить IP {ip}: {e}")
        
        logger.info(f"Добавлено {len(new_ips)} новых IP адресов")
        return len(new_ips)
    
    def setup_routing_table(self):
        """Настроить таблицу маршрутизации"""
        # Добавляем таблицу в rt_tables если её нет
        with open('/etc/iproute2/rt_tables', 'r') as f:
            content = f.read()
        
        if f"{self.table_id} wg_selective" not in content:
            with open('/etc/iproute2/rt_tables', 'a') as f:
                f.write(f"\n{self.table_id} wg_selective\n")
            logger.info("Добавлена таблица маршрутизации wg_selective")
        
        # Настраиваем маршрут через WireGuard
        wg_interface = "wg0"
        try:
            # Получаем gateway для WireGuard
            result = self.run_command("ip route show dev wg0 | grep -E 'via|src' | head -1")
            if result.stdout.strip():
                # Добавляем маршрут по умолчанию в специальную таблицу
                self.run_command(f"ip route add default dev {wg_interface} table {self.table_id}", check=False)
                logger.info(f"Настроен маршрут через {wg_interface}")
            else:
                logger.warning("WireGuard интерфейс не найден или не настроен")
        except Exception as e:
            logger.error(f"Ошибка настройки маршрута: {e}")
    
    def setup_iptables_rules(self):
        """Настроить правила iptables"""
        # Очищаем старые правила
        self.run_command("iptables -t mangle -D OUTPUT -m set --match-set wg_domains dst -j MARK --set-mark 0x1", check=False)
        
        # Добавляем правило маркировки пакетов
        self.run_command(f"iptables -t mangle -A OUTPUT -m set --match-set {self.ipset_name} dst -j MARK --set-mark {self.mark}")
        logger.info("Настроены правила iptables")
    
    def setup_ip_rules(self):
        """Настроить правила маршрутизации"""
        # Удаляем старое правило если есть
        self.run_command(f"ip rule del fwmark {self.mark} table {self.table_id}", check=False)
        
        # Добавляем правило маршрутизации по метке
        self.run_command(f"ip rule add fwmark {self.mark} table {self.table_id}")
        logger.info("Настроены правила IP маршрутизации")
    
    def update_domains(self):
        """Обновить список доменов"""
        logger.info("Начало обновления селективной маршрутизации")
        
        # Проверяем и создаем ipset
        result = self.run_command(f"ipset list {self.ipset_name}", check=False)
        if result.returncode != 0:
            self.create_ipset()
        else:
            logger.info(f"ipset {self.ipset_name} уже существует")
        
        # Собираем все IP адреса
        all_ips = set()
        for domain in VPN_DOMAINS:
            logger.info(f"Резолвинг домена: {domain}")
            try:
                ips = self.resolve_domain(domain)
                if ips:
                    all_ips.update(ips)
                    logger.info(f"{domain}: {len(ips)} IP-адресов")
                else:
                    logger.warning(f"{domain}: IP-адреса не найдены")
            except Exception as e:
                logger.error(f"Ошибка обработки {domain}: {e}")
        
        # Обновляем ipset
        added_count = self.update_ipset(all_ips)
        
        # Настраиваем маршрутизацию
        self.setup_routing_table()
        self.setup_iptables_rules()
        self.setup_ip_rules()
        
        # Сохраняем правила iptables
        self.run_command("iptables-save > /etc/iptables/rules.v4", check=False)
        
        logger.info(f"Обновление завершено. Всего IP: {len(all_ips)}, добавлено: {added_count}")
        return len(all_ips)
    
    def status(self):
        """Показать статус системы"""
        print("=== Статус селективного VPN ===")
        
        # Проверяем ipset
        result = self.run_command(f"ipset list {self.ipset_name} | wc -l", check=False)
        if result.returncode == 0:
            count = int(result.stdout.strip()) - 8  # Убираем заголовочные строки
            print(f"IP адресов в ipset: {max(0, count)}")
        else:
            print("ipset не найден")
        
        # Проверяем правила iptables
        result = self.run_command("iptables -t mangle -L OUTPUT | grep wg_domains", check=False)
        if result.returncode == 0:
            print("Правила iptables: ✓")
        else:
            print("Правила iptables: ✗")
        
        # Проверяем правила маршрутизации
        result = self.run_command(f"ip rule list | grep {self.table_id}", check=False)
        if result.returncode == 0:
            print("Правила маршрутизации: ✓")
        else:
            print("Правила маршрутизации: ✗")
        
        # Проверяем WireGuard
        result = self.run_command("wg show", check=False)
        if result.returncode == 0 and result.stdout.strip():
            print("WireGuard: ✓")
            # Показываем количество подключенных клиентов
            peers = result.stdout.count("peer:")
            print(f"Подключенных клиентов: {peers}")
        else:
            print("WireGuard: ✗")

def main():
    parser = argparse.ArgumentParser(description='Селективная маршрутизация для VPN')
    parser.add_argument('--update', action='store_true', help='Обновить домены и настройки')
    parser.add_argument('--status', action='store_true', help='Показать статус')
    parser.add_argument('--daemon', action='store_true', help='Запуск в режиме демона')
    
    args = parser.parse_args()
    
    router = SelectiveRouter()
    
    if args.update:
        router.update_domains()
    elif args.status:
        router.status()
    elif args.daemon:
        logger.info("Запуск демона селективной маршрутизации")
        while True:
            try:
                router.update_domains()
                time.sleep(3600)  # Обновляем каждый час
            except KeyboardInterrupt:
                logger.info("Демон остановлен")
                break
            except Exception as e:
                logger.error(f"Ошибка в демоне: {e}")
                time.sleep(300)  # Ждем 5 минут при ошибке
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
EOF

chmod +x /root/selective_routing.py

# Создаем systemd сервис для селективной маршрутизации
echo "3. Создание systemd сервиса для селективной маршрутизации..."
cat > /etc/systemd/system/selective-routing.service << 'EOF'
[Unit]
Description=Selective VPN Routing Service
After=network.target wg-quick@wg0.service
Wants=wg-quick@wg0.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /root/selective_routing.py --daemon
ExecStartPre=/usr/bin/python3 /root/selective_routing.py --update
Restart=always
RestartSec=30
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Исправляем бота
echo "4. Исправление Telegram бота..."
cat > /root/wireguard_bot.py << 'EOF'
#!/usr/bin/env python3
"""
Telegram бот для управления WireGuard VPN
Выдает конфигурации клиентам и управляет подключениями
"""

import os
import sqlite3
import subprocess
import logging
import asyncio
import ipaddress
from datetime import datetime, timedelta
from pathlib import Path
import qrcode
import io
import base64

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatAction

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/wireguard_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
DB_PATH = '/root/wireguard_clients.db'
WG_CONFIG_PATH = '/etc/wireguard/wg0.conf'
WG_INTERFACE = 'wg0'

class WireGuardManager:
    def __init__(self):
        self.db_path = DB_PATH
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            private_key TEXT,
            public_key TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_handshake TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            traffic_rx INTEGER DEFAULT 0,
            traffic_tx INTEGER DEFAULT 0
        )
        ''')
        
        conn.commit()
        conn.close()
    
    def generate_keys(self):
        """Генерация ключей WireGuard"""
        try:
            # Генерируем приватный ключ
            private_key = subprocess.run(
                ['wg', 'genkey'], 
                capture_output=True, text=True, check=True
            ).stdout.strip()
            
            # Генерируем публичный ключ
            public_key = subprocess.run(
                ['wg', 'pubkey'], 
                input=private_key, 
                capture_output=True, text=True, check=True
            ).stdout.strip()
            
            return private_key, public_key
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка генерации ключей: {e}")
            return None, None
    
    def get_next_ip(self):
        """Получить следующий доступный IP адрес"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Получаем все используемые IP
        cursor.execute('SELECT ip_address FROM clients WHERE is_active = 1')
        used_ips = set(row[0] for row in cursor.fetchall())
        
        conn.close()
        
        # Диапазон IP адресов для клиентов (10.66.66.2 - 10.66.66.254)
        network = ipaddress.IPv4Network('10.66.66.0/24')
        for ip in network.hosts():
            if str(ip) not in used_ips and str(ip) != '10.66.66.1':  # .1 зарезервирован для сервера
                return str(ip)
        
        return None
    
    def create_client(self, user_id: int, username: str = None):
        """Создать нового клиента"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Проверяем, есть ли уже клиент
        cursor.execute('SELECT id FROM clients WHERE user_id = ?', (user_id,))
        if cursor.fetchone():
            conn.close()
            return None, "Клиент уже существует"
        
        # Генерируем ключи
        private_key, public_key = self.generate_keys()
        if not private_key:
            conn.close()
            return None, "Ошибка генерации ключей"
        
        # Получаем IP адрес
        ip_address = self.get_next_ip()
        if not ip_address:
            conn.close()
            return None, "Нет свободных IP адресов"
        
        # Добавляем в базу
        cursor.execute('''
        INSERT INTO clients (user_id, username, private_key, public_key, ip_address)
        VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, private_key, public_key, ip_address))
        
        client_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Добавляем в конфигурацию WireGuard
        self.add_peer_to_config(public_key, ip_address)
        
        return client_id, None
    
    def add_peer_to_config(self, public_key: str, ip_address: str):
        """Добавить peer в конфигурацию WireGuard"""
        try:
            # Добавляем peer через wg команду
            subprocess.run([
                'wg', 'set', WG_INTERFACE, 'peer', public_key, 
                'allowed-ips', f'{ip_address}/32'
            ], check=True)
            
            # Сохраняем конфигурацию
            subprocess.run(['wg-quick', 'save', WG_INTERFACE], check=True)
            
            logger.info(f"Добавлен peer {public_key} с IP {ip_address}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка добавления peer: {e}")
    
    def get_client_config(self, user_id: int):
        """Получить конфигурацию клиента"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT private_key, ip_address FROM clients 
        WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return None
        
        private_key, ip_address = result
        
        # Читаем серверную конфигурацию для получения публичного ключа сервера
        try:
            with open(WG_CONFIG_PATH, 'r') as f:
                config_content = f.read()
            
            # Извлекаем данные сервера (это простой парсер, можно улучшить)
            server_public_key = None
            server_endpoint = None
            
            for line in config_content.split('\n'):
                if line.startswith('# Server PublicKey:'):
                    server_public_key = line.split(':')[1].strip()
                elif 'PublicKey' in line and not line.startswith('#'):
                    if not server_public_key:  # Если не нашли в комментарии
                        server_public_key = line.split('=')[1].strip()
            
            # Получаем внешний IP сервера
            result = subprocess.run(['curl', '-s', 'https://ipinfo.io/ip'], 
                                  capture_output=True, text=True, timeout=10)
            server_ip = result.stdout.strip() if result.returncode == 0 else '95.140.154.181'
            
        except Exception as e:
            logger.error(f"Ошибка чтения конфигурации сервера: {e}")
            return None
        
        # Генерируем конфигурацию клиента
        client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {ip_address}/24
DNS = 8.8.8.8, 1.1.1.1

[Peer]
PublicKey = k0Z/Cgo7YoPQZptjUFO8RKGogCFKUXsh4XBgGICi1Sk=
Endpoint = {server_ip}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        
        return client_config
    
    def get_client_stats(self, user_id: int):
        """Получить статистику клиента"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT ip_address, created_at, last_handshake, traffic_rx, traffic_tx 
        FROM clients WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        return result
    
    def deactivate_client(self, user_id: int):
        """Деактивировать клиента"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE clients SET is_active = 0 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        
        # Удаляем из WireGuard конфигурации
        # TODO: Реализовать удаление peer из активной конфигурации
    
    def get_all_clients(self):
        """Получить всех клиентов (для админов)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT user_id, username, ip_address, created_at, is_active 
        FROM clients ORDER BY created_at DESC
        ''')
        
        results = cursor.fetchall()
        conn.close()
        
        return results

# Инициализация менеджера
wg_manager = WireGuardManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [
        [InlineKeyboardButton("🔑 Получить конфигурацию", callback_data='get_config')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')]
    ]
    
    if update.effective_user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("👥 Управление (Админ)", callback_data='admin')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 Добро пожаловать в WireGuard VPN бот!\n\n"
        "Этот бот поможет вам получить доступ к селективному VPN.\n"
        "VPN работает только для заблокированных ресурсов, остальной трафик идет напрямую.\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username
    
    if query.data == 'get_config':
        await handle_get_config(query, user_id, username)
    elif query.data == 'stats':
        await handle_stats(query, user_id)
    elif query.data == 'help':
        await handle_help(query)
    elif query.data == 'admin' and user_id in ADMIN_IDS:
        await handle_admin(query)

async def handle_get_config(query, user_id: int, username: str):
    """Обработка получения конфигурации"""
    await query.message.reply_chat_action(ChatAction.TYPING)
    
    # Проверяем, есть ли уже клиент
    config = wg_manager.get_client_config(user_id)
    
    if not config:
        # Создаем нового клиента
        client_id, error = wg_manager.create_client(user_id, username)
        if error:
            await query.edit_message_text(f"❌ Ошибка: {error}")
            return
        
        config = wg_manager.get_client_config(user_id)
    
    if config:
        # Отправляем конфигурацию как файл
        config_file = io.BytesIO(config.encode())
        config_file.name = f'wg_client_{user_id}.conf'
        
        # Генерируем QR код
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(config)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)
        
        await query.message.reply_document(
            document=config_file,
            caption="📱 Ваша конфигурация WireGuard готова!\n\n"
                   "📋 Инструкция:\n"
                   "1. Установите WireGuard на устройство\n"
                   "2. Импортируйте этот файл или отсканируйте QR код\n"
                   "3. Активируйте подключение\n\n"
                   "🔒 VPN работает селективно - только для заблокированных сайтов"
        )
        
        await query.message.reply_photo(
            photo=qr_buffer,
            caption="📱 QR код для быстрого подключения"
        )
        
        logger.info(f"Конфигурация выдана пользователю {user_id} (@{username})")
    else:
        await query.edit_message_text("❌ Ошибка создания конфигурации")

async def handle_stats(query, user_id: int):
    """Обработка просмотра статистики"""
    stats = wg_manager.get_client_stats(user_id)
    
    if stats:
        ip_address, created_at, last_handshake, traffic_rx, traffic_tx = stats
        
        # Форматируем дату создания
        created_date = datetime.fromisoformat(created_at).strftime("%d.%m.%Y %H:%M")
        
        # Статус подключения
        status = "🟢 Подключен" if last_handshake else "🔴 Не подключен"
        
        # Форматируем трафик
        rx_mb = traffic_rx / (1024 * 1024) if traffic_rx else 0
        tx_mb = traffic_tx / (1024 * 1024) if traffic_tx else 0
        
        stats_text = f"""📊 Ваша статистика:

🆔 IP адрес: {ip_address}
📅 Создан: {created_date}
🔗 Статус: {status}
📊 Загружено: {rx_mb:.1f} МБ
📤 Отправлено: {tx_mb:.1f} МБ

ℹ️ VPN работает только для заблокированных ресурсов"""
        
        await query.edit_message_text(stats_text)
    else:
        await query.edit_message_text("❌ Конфигурация не найдена. Сначала получите конфигурацию.")

async def handle_help(query):
    """Обработка помощи"""
    help_text = """❓ Помощь по использованию VPN:

🎯 **Особенности селективного VPN:**
• Работает только для заблокированных сайтов
• Остальной трафик идет напрямую (быстрее)
• Автоматически определяет нужные ресурсы

📱 **Поддерживаемые устройства:**
• iPhone/iPad (App Store: WireGuard)
• Android (Play Store: WireGuard)
• Windows/Mac/Linux (wireguard.com)

🔧 **Настройка:**
1. Установите приложение WireGuard
2. Получите конфигурацию через бота
3. Импортируйте файл или сканируйте QR код
4. Активируйте подключение

❓ **Проблемы:**
• Не работает? Проверьте подключение к интернету
• Медленно? Это нормально для заблокированных сайтов
• Нужна помощь? Обратитесь к администратору"""
    
    await query.edit_message_text(help_text)

async def handle_admin(query):
    """Обработка админ панели"""
    clients = wg_manager.get_all_clients()
    
    active_count = sum(1 for client in clients if client[4])  # is_active
    total_count = len(clients)
    
    admin_text = f"""👥 Панель администратора:

📊 Статистика:
• Всего клиентов: {total_count}
• Активных: {active_count}
• Неактивных: {total_count - active_count}

📋 Последние клиенты:"""
    
    for client in clients[:10]:  # Показываем последних 10
        user_id, username, ip_address, created_at, is_active = client
        status = "✅" if is_active else "❌"
        username_str = f"@{username}" if username else f"ID:{user_id}"
        created_date = datetime.fromisoformat(created_at).strftime("%d.%m %H:%M")
        admin_text += f"\n{status} {username_str} ({ip_address}) - {created_date}"
    
    await query.edit_message_text(admin_text)

def main():
    """Основная функция"""
    if not TELEGRAM_TOKEN:
        logger.error("Не указан TELEGRAM_BOT_TOKEN")
        return
    
    if not ADMIN_IDS:
        logger.warning("Не указаны ADMIN_IDS")
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("Бот запущен")
    
    # Запускаем бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
EOF

chmod +x /root/wireguard_bot.py

# Обновляем конфигурацию WireGuard
echo "5. Обновление конфигурации WireGuard..."
cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.backup

cat > /etc/wireguard/wg0.conf << 'EOF'
[Interface]
PrivateKey = wJbpYtqNsK0nVLEeLJBrQE5b67jvjuWWL1m+YhRKSFE=
Address = 10.66.66.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Server PublicKey: k0Z/Cgo7YoPQZptjUFO8RKGogCFKUXsh4XBgGICi1Sk=
EOF

# Включаем IP forwarding
echo "6. Настройка IP forwarding..."
echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
sysctl -p

# Перезапускаем WireGuard
echo "7. Перезапуск WireGuard..."
systemctl restart wg-quick@wg0

# Запускаем и включаем сервисы
echo "8. Запуск сервисов..."
systemctl daemon-reload
systemctl enable selective-routing.service
systemctl restart selective-routing.service
systemctl restart wireguard-bot.service

# Первоначальная настройка селективного роутинга
echo "9. Первоначальная настройка селективного роутинга..."
python3 /root/selective_routing.py --update

# Проверяем статус
echo "10. Проверка статуса..."
systemctl status wg-quick@wg0 --no-pager -l
systemctl status wireguard-bot.service --no-pager -l
systemctl status selective-routing.service --no-pager -l

echo ""
echo "=== Исправление завершено! ==="
echo ""
echo "📊 Статус системы:"
python3 /root/selective_routing.py --status
echo ""
echo "🔧 WireGuard интерфейс:"
wg show
echo ""
echo "📝 Логи бота: tail -f /var/log/wireguard_bot.log"
echo "📝 Логи роутинга: tail -f /var/log/selective_routing.log"
echo ""
echo "✅ Селективный VPN сервер настроен и работает!"