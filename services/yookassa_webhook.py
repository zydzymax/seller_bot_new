"""
YooKassa Payment Webhook Handler
Обработка платежей и активация лицензий
"""

from dotenv import load_dotenv

load_dotenv()

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta

import asyncpg
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Configuration
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
SALES_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

# Plans configuration
PLANS = {
    "ai_seller": {
        "price": 1990,
        "days": 30,
        "ai_seller": True,
        "head_of_sales": False,
        "name": "AI Seller",
    },
    "head_of_sales": {
        "price": 2990,
        "days": 30,
        "ai_seller": False,
        "head_of_sales": True,
        "name": "Head of Sales",
    },
    "suite": {
        "price": 3990,
        "days": 30,
        "ai_seller": True,
        "head_of_sales": True,
        "name": "Suite",
    },
}


async def send_telegram(message: str):
    """Send notification to Telegram"""
    if not SALES_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{SALES_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        logger.error(f"Telegram error: {e}")


async def process_payment(payment_data: dict) -> bool:
    """Process successful payment and activate license"""
    try:
        payment_id = payment_data.get("id")
        status = payment_data.get("status")
        amount = float(payment_data.get("amount", {}).get("value", 0))
        metadata = payment_data.get("metadata", {})

        license_id = metadata.get("license_id")
        plan = metadata.get("plan", "suite")
        email = metadata.get("email", "")

        if status != "succeeded":
            logger.info(f"Payment {payment_id} status: {status} (not succeeded)")
            return False

        if not license_id:
            logger.warning(f"Payment {payment_id} has no license_id in metadata")
            # Try to find by email
            if not email:
                await send_telegram(
                    f"⚠️ Платёж без license_id!\nID: {payment_id}\nСумма: {amount}₽"
                )
                return False

        plan_info = PLANS.get(plan, PLANS["suite"])

        pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=2)

        async with pool.acquire() as conn:
            # Find or create license
            if license_id:
                license_data = await conn.fetchrow(
                    "SELECT * FROM widget_licenses WHERE id = $1", int(license_id)
                )
            else:
                license_data = await conn.fetchrow(
                    "SELECT * FROM widget_licenses WHERE email = $1 ORDER BY created_at DESC LIMIT 1",
                    email,
                )

            if not license_data:
                logger.error(f"License not found for payment {payment_id}")
                await send_telegram(
                    f"⚠️ Лицензия не найдена!\nPayment: {payment_id}\nEmail: {email}"
                )
                await pool.close()
                return False

            # Calculate new expiration
            now = datetime.now()
            current_expires = license_data["expires_at"]
            if current_expires and current_expires > now:
                new_expires = current_expires + timedelta(days=plan_info["days"])
            else:
                new_expires = now + timedelta(days=plan_info["days"])

            # Update license
            await conn.execute(
                """
                UPDATE widget_licenses 
                SET plan = $1, status = 'active', expires_at = $2,
                    module_ai_seller = $3, module_head_of_sales = $4,
                    last_payment_at = NOW(), last_payment_amount = $5
                WHERE id = $6
            """,
                plan,
                new_expires,
                plan_info["ai_seller"],
                plan_info["head_of_sales"],
                amount,
                license_data["id"],
            )

            # Record payment
            await conn.execute(
                """
                INSERT INTO widget_payments (license_id, payment_id, amount, status, plan, provider, created_at)
                VALUES ($1, $2, $3, 'succeeded', $4, 'yookassa', NOW())
            """,
                license_data["id"],
                payment_id,
                amount,
                plan,
            )

        await pool.close()

        # Send success notification
        key = license_data["license_key"]
        await send_telegram(
            f"💰 <b>Оплата получена!</b>\n\n"
            f"📧 {email or license_data.get('email', 'N/A')}\n"
            f"📦 Тариф: {plan_info['name']}\n"
            f"💵 Сумма: {amount:,.0f}₽\n"
            f"🔑 Ключ: <code>{key}</code>\n"
            f"📅 Активен до: {new_expires.strftime('%d.%m.%Y')}\n\n"
            f"✅ Лицензия активирована автоматически!"
        )

        logger.info(f"Payment {payment_id} processed successfully")
        return True

    except Exception as e:
        logger.error(f"Payment processing error: {e}")
        await send_telegram(f"❌ Ошибка обработки платежа!\n{str(e)[:200]}")
        return False


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for YooKassa webhooks"""

    def do_POST(self):
        if self.path == "/api/payment/webhook" or self.path == "/webhook":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                data = json.loads(body.decode("utf-8"))
                event = data.get("event")
                payment = data.get("object", {})

                logger.info(f"Received webhook: {event}")

                if event == "payment.succeeded":
                    asyncio.run(process_payment(payment))
                elif event == "payment.canceled":
                    asyncio.run(
                        send_telegram(f"❌ Платёж отменён: {payment.get('id')}")
                    )

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            except Exception as e:
                logger.error(f"Webhook error: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "service": "yookassa-webhook"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {args[0]}")


def run_server(port=8080):
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    logger.info(f"YooKassa webhook server started on port {port}")
    asyncio.run(send_telegram("💳 YooKassa webhook сервер запущен"))
    server.serve_forever()


if __name__ == "__main__":
    run_server(8080)
