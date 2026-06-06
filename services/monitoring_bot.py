"""
Sales Whisper Suite - Monitoring Bot
Monitors all systems and sends alerts to Telegram
"""

from dotenv import load_dotenv

load_dotenv()

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

import httpx
import asyncpg

logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("MONITORING_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
REDIS_URL = os.getenv("REDIS_URL", "")

# Endpoints to monitor
ENDPOINTS = {
    "SalesWhisper Site": "https://saleswhisper.pro",
    "SalesWhisper API": "https://saleswhisper.pro/api/health",
    "Widget API": "https://saleswhisper.pro/api/widget/health",
    "Crosspost Site": "https://crosspost.saleswhisper.pro",
    "HeadOfSales Site": "https://headofsales.saleswhisper.pro",
}

# Check interval in seconds
CHECK_INTERVAL = 300  # 5 minutes

# Alert cooldown (don't spam alerts)
_alert_cooldown: Dict[str, datetime] = {}
ALERT_COOLDOWN_MINUTES = 30


class MonitoringBot:
    def __init__(self):
        self.http_client = None
        self.db_pool = None
        self.status_history: Dict[str, List[bool]] = {}

    async def init(self):
        """Initialize connections"""
        self.http_client = httpx.AsyncClient(timeout=10)
        try:
            self.db_pool = await asyncpg.create_pool(
                POSTGRES_DSN, min_size=1, max_size=3
            )
        except Exception as e:
            logger.error(f"Database connection failed: {e}")

    async def close(self):
        """Close connections"""
        if self.http_client:
            await self.http_client.aclose()
        if self.db_pool:
            await self.db_pool.close()

    async def send_alert(self, message: str, force: bool = False):
        """Send alert to Telegram"""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
            logger.warning("Telegram not configured")
            return

        try:
            await self.http_client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_notification": not force,
                },
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def can_send_alert(self, key: str) -> bool:
        """Check if we can send alert (cooldown)"""
        now = datetime.now()
        if key in _alert_cooldown:
            if now - _alert_cooldown[key] < timedelta(minutes=ALERT_COOLDOWN_MINUTES):
                return False
        _alert_cooldown[key] = now
        return True

    async def check_endpoint(self, name: str, url: str) -> Dict[str, Any]:
        """Check single endpoint"""
        start_time = datetime.now()
        try:
            response = await self.http_client.get(url)
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            return {
                "name": name,
                "url": url,
                "status": "up" if response.status_code == 200 else "degraded",
                "status_code": response.status_code,
                "response_time_ms": round(response_time, 2),
                "error": None,
            }
        except Exception as e:
            return {
                "name": name,
                "url": url,
                "status": "down",
                "status_code": 0,
                "response_time_ms": 0,
                "error": str(e),
            }

    async def check_database(self) -> Dict[str, Any]:
        """Check PostgreSQL"""
        try:
            if not self.db_pool:
                return {
                    "name": "PostgreSQL",
                    "status": "down",
                    "error": "No connection",
                }

            async with self.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            return {"name": "PostgreSQL", "status": "up", "error": None}
        except Exception as e:
            return {"name": "PostgreSQL", "status": "down", "error": str(e)}

    async def check_redis(self) -> Dict[str, Any]:
        """Check Redis"""
        try:
            import redis.asyncio as redis

            r = redis.from_url(REDIS_URL)
            await r.ping()
            await r.close()
            return {"name": "Redis", "status": "up", "error": None}
        except Exception as e:
            return {"name": "Redis", "status": "down", "error": str(e)}

    async def run_all_checks(self) -> Dict[str, Any]:
        """Run all health checks"""
        results = {
            "timestamp": datetime.now().isoformat(),
            "endpoints": [],
            "services": [],
            "overall_status": "up",
        }

        # Check endpoints
        for name, url in ENDPOINTS.items():
            result = await self.check_endpoint(name, url)
            results["endpoints"].append(result)

            # Track status history
            if name not in self.status_history:
                self.status_history[name] = []
            self.status_history[name].append(result["status"] == "up")
            self.status_history[name] = self.status_history[name][-10:]  # Keep last 10

            if result["status"] == "down":
                results["overall_status"] = "degraded"

        # Check services
        db_result = await self.check_database()
        results["services"].append(db_result)
        if db_result["status"] == "down":
            results["overall_status"] = "critical"

        redis_result = await self.check_redis()
        results["services"].append(redis_result)

        return results

    async def process_results(self, results: Dict[str, Any]):
        """Process results and send alerts if needed"""
        alerts = []
        recoveries = []

        # Check endpoints
        for endpoint in results["endpoints"]:
            name = endpoint["name"]
            status = endpoint["status"]

            if status == "down":
                if self.can_send_alert(f"down_{name}"):
                    alerts.append(
                        f"🔴 <b>{name}</b> недоступен\n   Ошибка: {endpoint['error']}"
                    )
            elif status == "degraded":
                if self.can_send_alert(f"degraded_{name}"):
                    alerts.append(
                        f"🟡 <b>{name}</b> работает с ошибками\n   Код: {endpoint['status_code']}"
                    )
            else:
                # Check if recovered
                history = self.status_history.get(name, [])
                if len(history) >= 2 and not history[-2] and history[-1]:
                    recoveries.append(
                        f"🟢 <b>{name}</b> восстановлен\n   Время отклика: {endpoint['response_time_ms']}ms"
                    )

        # Check services
        for service in results["services"]:
            name = service["name"]
            if service["status"] == "down":
                if self.can_send_alert(f"service_{name}"):
                    alerts.append(
                        f"🔴 <b>{name}</b> недоступен\n   Ошибка: {service['error']}"
                    )

        # Send alerts
        if alerts:
            message = "⚠️ <b>АЛЕРТ - Sales Whisper Monitoring</b>\n\n" + "\n\n".join(
                alerts
            )
            message += f"\n\n🕐 {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
            await self.send_alert(message, force=True)

        # Send recoveries
        if recoveries:
            message = "✅ <b>Восстановление</b>\n\n" + "\n\n".join(recoveries)
            await self.send_alert(message)

    async def get_status_report(self) -> str:
        """Generate status report"""
        results = await self.run_all_checks()

        status_emoji = {"up": "🟢", "degraded": "🟡", "down": "🔴", "critical": "🔴"}

        report = "📊 <b>Статус системы Sales Whisper</b>\n"
        report += f"🕐 {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n\n"

        report += "<b>Сайты и API:</b>\n"
        for ep in results["endpoints"]:
            emoji = status_emoji.get(ep["status"], "⚪")
            time_str = f" ({ep['response_time_ms']}ms)" if ep["status"] == "up" else ""
            report += f"{emoji} {ep['name']}{time_str}\n"

        report += "\n<b>Сервисы:</b>\n"
        for svc in results["services"]:
            emoji = status_emoji.get(svc["status"], "⚪")
            report += f"{emoji} {svc['name']}\n"

        overall_emoji = status_emoji.get(results["overall_status"], "⚪")
        report += f"\n<b>Общий статус:</b> {overall_emoji} {results['overall_status'].upper()}"

        return report

    async def run_forever(self):
        """Main monitoring loop"""
        await self.init()
        logger.info("Monitoring bot started")

        # Send startup message
        await self.send_alert("🚀 <b>Мониторинг Sales Whisper запущен</b>")

        try:
            while True:
                try:
                    results = await self.run_all_checks()
                    await self.process_results(results)
                except Exception as e:
                    logger.error(f"Monitoring check error: {e}")

                await asyncio.sleep(CHECK_INTERVAL)
        finally:
            await self.close()


# Command handler for manual status check
async def handle_status_command():
    """Handle /status command"""
    bot = MonitoringBot()
    await bot.init()
    report = await bot.get_status_report()
    await bot.send_alert(report)
    await bot.close()


# Run as standalone script
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s"
    )

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        asyncio.run(handle_status_command())
    else:
        bot = MonitoringBot()
        asyncio.run(bot.run_forever())
