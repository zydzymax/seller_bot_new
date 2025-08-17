# 🛡️ Инструкция по деплою SoVAni AI-продавца из РФ через VPN

## 📋 Оглавление
1. [Подготовка VPN инфраструктуры](#подготовка-vpn)
2. [Настройка окружения](#настройка-окружения) 
3. [Оптимизация для VPN](#оптимизация-для-vpn)
4. [Мониторинг и устранение проблем](#мониторинг)
5. [Рекомендации по безопасности](#безопасность)

---

## 🔧 Подготовка VPN инфраструктуры {#подготовка-vpn}

### Требования к VPN серверу:
- **Локация**: Европа (Германия, Нидерланды) или США (Восточное побережье)
- **Пропускная способность**: минимум 100 Мбит/с
- **Ping до OpenAI/Anthropic**: < 100ms
- **Протокол**: WireGuard (рекомендуется) или OpenVPN

### Рекомендуемые VPN провайдеры:
1. **Mullvad** - WireGuard, серверы в Германии
2. **ProtonVPN** - высокая надежность  
3. **ExpressVPN** - стабильные серверы в Европе
4. **Собственный VPS** - максимальный контроль

### Настройка WireGuard на сервере:

```bash
# На VPN сервере (Ubuntu/Debian)
apt update && apt install wireguard

# Генерация ключей
cd /etc/wireguard
umask 077
wg genkey | tee server_private_key | wg pubkey > server_public_key
wg genkey | tee client_private_key | wg pubkey > client_public_key

# Конфигурация сервера (/etc/wireguard/wg0.conf)
cat > wg0.conf << EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat server_private_key)
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
PublicKey = $(cat client_public_key)
AllowedIPs = 10.0.0.2/32
EOF

# Запуск
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0
```

### Конфигурация клиента (Россия):

```bash
# /etc/wireguard/wg0-client.conf
[Interface]
Address = 10.0.0.2/24
PrivateKey = CLIENT_PRIVATE_KEY
DNS = 1.1.1.1

[Peer]
PublicKey = SERVER_PUBLIC_KEY
Endpoint = YOUR_VPN_SERVER_IP:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

---

## ⚙️ Настройка окружения {#настройка-окружения}

### 1. Environment переменные для VPN

Создайте `.env.vpn`:

```bash
# VPN настройки
USE_VPN=true
VPN_GATEWAY=10.0.0.1
VPN_DNS=1.1.1.1
VPN_CHECK_INTERVAL=60

# Увеличенные таймауты для VPN
OPENAI_TIMEOUT=60
ANTHROPIC_TIMEOUT=70
ELEVENLABS_TIMEOUT=120

# Connection pooling
HTTP_POOL_SIZE=10
HTTP_POOL_PER_HOST=5
HTTP_KEEPALIVE_TIMEOUT=300

# Retry настройки  
MAX_RETRIES=4
RETRY_BACKOFF_FACTOR=2
CIRCUIT_BREAKER_FAILURES=3
CIRCUIT_BREAKER_TIMEOUT=60

# Redis с увеличенными таймаутами
REDIS_URL=redis://localhost:6379/0
REDIS_SOCKET_TIMEOUT=30
REDIS_SOCKET_CONNECT_TIMEOUT=10

# API ключи
TELEGRAM_TOKEN=your_bot_token
OPENAI_API_KEY=sk-your_openai_key
ANTHROPIC_API_KEY=sk-ant-your_claude_key
ELEVENLABS_API_KEY=sk_6c469661d89b8ef2069f645f239b13c408d795e52fe1ab99

# Мониторинг
ENABLE_METRICS=true
METRICS_PORT=8090
LOG_LEVEL=INFO
```

### 2. Docker Compose для VPN деплоя

Обновите `docker-compose.vpn.yml`:

```yaml
version: '3.8'

services:
  ai_seller:
    build: .
    environment:
      - USE_VPN=true
      - OPENAI_TIMEOUT=60
      - ANTHROPIC_TIMEOUT=70
      - ELEVENLABS_TIMEOUT=120
    env_file:
      - .env.vpn
    volumes:
      - ./logs:/app/logs
      - /etc/wireguard:/etc/wireguard:ro
    network_mode: host  # Для доступа к VPN интерфейсу
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun
    restart: unless-stopped
    
  redis:
    image: redis:7-alpine
    command: >
      redis-server 
      --timeout 30
      --tcp-keepalive 300
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    restart: unless-stopped
    
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: ai_seller
      POSTGRES_USER: ai_seller
      POSTGRES_PASSWORD: secure_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/migrate_full_schema.sql:/docker-entrypoint-initdb.d/init.sql
    restart: unless-stopped
    
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deployments/nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/ssl/certs
    depends_on:
      - ai_seller
    restart: unless-stopped

  # Мониторинг VPN соединения
  vpn_monitor:
    build:
      context: .
      dockerfile: Dockerfile.vpn-monitor
    environment:
      - VPN_GATEWAY=10.0.0.1
      - CHECK_INTERVAL=60
      - ALERT_WEBHOOK=https://your-webhook-url
    network_mode: host
    restart: unless-stopped

volumes:
  redis_data:
  postgres_data:
```

### 3. VPN мониторинг контейнер

Создайте `Dockerfile.vpn-monitor`:

```dockerfile
FROM python:3.11-alpine

RUN apk add --no-cache curl ping

COPY scripts/vpn_monitor.py /app/vpn_monitor.py
WORKDIR /app

CMD ["python", "vpn_monitor.py"]
```

Создайте `scripts/vpn_monitor.py`:

```python
#!/usr/bin/env python3
"""
VPN мониторинг для SoVAni AI-продавца
Проверяет доступность VPN и отправляет алерты
"""

import time
import subprocess
import requests
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vpn_monitor")

VPN_GATEWAY = os.getenv("VPN_GATEWAY", "10.0.0.1")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
ALERT_WEBHOOK = os.getenv("ALERT_WEBHOOK")

# Эндпоинты для проверки
TEST_ENDPOINTS = [
    "https://api.openai.com/v1/models",
    "https://api.anthropic.com/v1/messages", 
    "https://api.elevenlabs.io/v1/voices"
]

def check_vpn_connection():
    """Проверка VPN соединения"""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "5", VPN_GATEWAY],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except:
        return False

def check_external_access():
    """Проверка доступа к внешним API"""
    for endpoint in TEST_ENDPOINTS:
        try:
            response = requests.head(endpoint, timeout=10)
            if response.status_code in [200, 401, 405]:  # 401/405 = API работает
                return True
        except:
            continue
    return False

def send_alert(message):
    """Отправка алерта в webhook"""
    if not ALERT_WEBHOOK:
        return
    
    try:
        requests.post(ALERT_WEBHOOK, json={
            "text": f"🚨 SoVAni VPN Alert: {message}",
            "timestamp": time.time()
        }, timeout=10)
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")

def main():
    logger.info("Starting VPN monitor...")
    
    vpn_down_count = 0
    api_down_count = 0
    
    while True:
        # Проверка VPN
        if check_vpn_connection():
            if vpn_down_count > 0:
                logger.info("VPN connection restored")
                send_alert("VPN connection restored")
                vpn_down_count = 0
        else:
            vpn_down_count += 1
            logger.warning(f"VPN down for {vpn_down_count * CHECK_INTERVAL}s")
            
            if vpn_down_count == 3:  # 3 минуты
                send_alert("VPN connection lost!")
        
        # Проверка API доступа
        if check_external_access():
            if api_down_count > 0:
                logger.info("External API access restored")
                send_alert("External API access restored")
                api_down_count = 0
        else:
            api_down_count += 1
            logger.warning(f"External APIs unreachable for {api_down_count * CHECK_INTERVAL}s")
            
            if api_down_count == 2:  # 2 минуты
                send_alert("External APIs unreachable!")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
```

---

## 🚀 Оптимизация для VPN {#оптимизация-для-vpn}

### 1. Обновление конфигурации Python

Создайте `config/vpn_config.py`:

```python
"""
VPN-оптимизированные настройки для SoVAni
"""

VPN_SETTINGS = {
    # HTTP клиенты
    "http_timeout": 60,
    "connect_timeout": 30,
    "read_timeout": 45,
    "pool_connections": 10,
    "pool_maxsize": 20,
    "max_retries": 4,
    "backoff_factor": 2,
    
    # LLM провайдеры
    "openai_timeout": 60,
    "anthropic_timeout": 70,
    "elevenlabs_timeout": 120,
    
    # Circuit breaker
    "circuit_breaker_failures": 3,
    "circuit_breaker_timeout": 60,
    "circuit_breaker_reset_timeout": 120,
    
    # Кэширование
    "redis_socket_timeout": 30,
    "redis_socket_connect_timeout": 10,
    "cache_ttl_multiplier": 2,  # Увеличиваем TTL для VPN
    
    # Worker'ы
    "max_workers": 3,
    "worker_timeout": 180,
    "queue_timeout": 300,
}

def apply_vpn_optimizations():
    """Применить VPN оптимизации к HTTP клиентам"""
    import aiohttp
    import asyncio
    
    # Настройки для aiohttp
    connector = aiohttp.TCPConnector(
        limit=VPN_SETTINGS["pool_connections"],
        limit_per_host=VPN_SETTINGS["pool_maxsize"] // 2,
        keepalive_timeout=300,
        enable_cleanup_closed=True,
        # Увеличиваем таймауты для VPN
        sock_connect=VPN_SETTINGS["connect_timeout"],
        sock_read=VPN_SETTINGS["read_timeout"]
    )
    
    timeout = aiohttp.ClientTimeout(
        total=VPN_SETTINGS["http_timeout"],
        connect=VPN_SETTINGS["connect_timeout"],
        sock_read=VPN_SETTINGS["read_timeout"]
    )
    
    return connector, timeout
```

### 2. Системные оптимизации

Добавьте в `scripts/optimize_vpn.sh`:

```bash
#!/bin/bash
# Оптимизация системы для работы через VPN

echo "Оптимизация сетевых настроек для VPN..."

# TCP оптимизации
sysctl -w net.core.rmem_max=16777216
sysctl -w net.core.wmem_max=16777216
sysctl -w net.ipv4.tcp_rmem="4096 87380 16777216"
sysctl -w net.ipv4.tcp_wmem="4096 65536 16777216"
sysctl -w net.ipv4.tcp_congestion_control=bbr

# Увеличиваем таймауты TCP
sysctl -w net.ipv4.tcp_keepalive_time=300
sysctl -w net.ipv4.tcp_keepalive_intvl=60
sysctl -w net.ipv4.tcp_keepalive_probes=3

# DNS оптимизации
echo "nameserver 1.1.1.1" > /etc/resolv.conf
echo "nameserver 8.8.8.8" >> /etc/resolv.conf

# Файловые дескрипторы
ulimit -n 65536

echo "VPN оптимизации применены"
```

### 3. Мониторинг производительности

Создайте `scripts/vpn_performance_check.py`:

```python
#!/usr/bin/env python3
"""
Проверка производительности через VPN
"""

import asyncio
import aiohttp
import time
import statistics

async def check_latency(url, timeout=30):
    """Проверка латентности до эндпоинта"""
    try:
        start = time.time()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.head(url) as response:
                latency = (time.time() - start) * 1000
                return latency, response.status
    except Exception as e:
        return None, str(e)

async def performance_test():
    """Тест производительности VPN"""
    endpoints = {
        "OpenAI": "https://api.openai.com/v1/models",
        "Anthropic": "https://api.anthropic.com/v1/messages",
        "ElevenLabs": "https://api.elevenlabs.io/v1/voices",
        "Google": "https://www.google.com",
    }
    
    print("🔍 Тестирование производительности VPN...")
    print("-" * 50)
    
    for name, url in endpoints.items():
        latencies = []
        successes = 0
        
        print(f"Тестирование {name}...")
        
        for i in range(5):
            latency, status = await check_latency(url)
            if latency:
                latencies.append(latency)
                successes += 1
                print(f"  Попытка {i+1}: {latency:.1f}ms (status: {status})")
            else:
                print(f"  Попытка {i+1}: FAILED ({status})")
            
            await asyncio.sleep(1)
        
        if latencies:
            avg_latency = statistics.mean(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)
            
            print(f"  ✅ Результат: {successes}/5 успешных")
            print(f"  📊 Латентность: avg={avg_latency:.1f}ms, min={min_latency:.1f}ms, max={max_latency:.1f}ms")
        else:
            print(f"  ❌ Все запросы не удались")
        
        print()

if __name__ == "__main__":
    asyncio.run(performance_test())
```

---

## 📊 Мониторинг и устранение проблем {#мониторинг}

### Команды для диагностики:

```bash
# Проверка VPN соединения
ping -c 5 10.0.0.1

# Проверка внешнего IP
curl ifconfig.me

# Трассировка до OpenAI
traceroute api.openai.com

# Тест скорости
curl -w "@curl-format.txt" -o /dev/null -s https://api.openai.com/v1/models

# Логи WireGuard
journalctl -u wg-quick@wg0 -f

# Мониторинг трафика
iftop -i wg0
```

### Частые проблемы и решения:

1. **VPN отключается**
   ```bash
   # Автоматический рестарт
   systemctl enable wg-quick@wg0
   # Добавить в cron проверку каждые 5 минут
   */5 * * * * /scripts/check_vpn.sh
   ```

2. **Высокая латентность**
   - Смените VPN сервер на ближайший
   - Включите BBR congestion control
   - Проверьте загрузку канала

3. **Блокировка API**
   - Смените IP адрес VPN сервера
   - Используйте вращение серверов
   - Добавьте случайные задержки между запросами

4. **Нестабильное соединение**
   ```bash
   # Увеличить PersistentKeepalive
   PersistentKeepalive = 15
   
   # Настроить MTU
   MTU = 1420
   ```

---

## 🔒 Рекомендации по безопасности {#безопасность}

### 1. Безопасность VPN:
- Используйте только WireGuard или OpenVPN
- Регулярно меняйте ключи (раз в месяц)
- Настройте kill switch для блокировки трафика при обрыве VPN
- Логируйте все VPN соединения

### 2. Сокрытие трафика:
```bash
# Обфускация WireGuard трафика
iptables -t mangle -A OUTPUT -p udp --dport 51820 -j MARK --set-mark 1
ip rule add fwmark 1 table 100
ip route add default via YOUR_VPN_GATEWAY table 100
```

### 3. API ключи:
- Храните в зашифрованных переменных окружения
- Используйте разные ключи для разных серверов
- Настройте ротацию ключей
- Мониторьте использование квот

### 4. Логирование:
```yaml
# docker-compose.logging.yml
version: '3.8'

services:
  ai_seller:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    environment:
      - LOG_LEVEL=INFO
      - MASK_SECRETS=true
```

---

## 🚀 Запуск системы

### Полная последовательность деплоя:

```bash
# 1. Подготовка сервера
./scripts/optimize_vpn.sh

# 2. Настройка VPN
wg-quick up wg0

# 3. Проверка производительности  
python3 scripts/vpn_performance_check.py

# 4. Запуск приложения
docker-compose -f docker-compose.vpn.yml up -d

# 5. Проверка логов
docker-compose logs -f ai_seller

# 6. Тест функциональности
curl http://localhost:8082/health
```

### Мониторинг работы:

```bash
# Статистика VPN
wg show

# Метрики приложения
curl http://localhost:8090/metrics

# Логи в реальном времени
tail -f logs/ai_seller.log | grep -E "(ERROR|WARNING|VPN)"
```

---

## 📞 Поддержка

При проблемах:

1. **Проверьте VPN**: `ping 10.0.0.1`
2. **Проверьте DNS**: `nslookup api.openai.com`
3. **Проверьте логи**: `docker-compose logs ai_seller`
4. **Запустите диагностику**: `python3 scripts/vpn_performance_check.py`

**Контакты поддержки**: Алена из SoVAni 😊

---

*© SoVAni 2025 | Безопасный AI-продавец через VPN* 🛡️