"""
Точка Банк Integration - Open Banking API
Автоматическое отслеживание поступлений для подтверждения счетов
"""

from dotenv import load_dotenv

load_dotenv()

import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import quote

import httpx
import asyncpg

logger = logging.getLogger(__name__)

# Configuration
TOCHKA_JWT_TOKEN = os.getenv("TOCHKA_JWT_TOKEN", "")
TOCHKA_ACCOUNT_ID = os.getenv("TOCHKA_ACCOUNT_ID", "")
TOCHKA_CLIENT_ID = os.getenv("TOCHKA_CLIENT_ID", "")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
SALES_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

TOCHKA_API_URL = "https://enter.tochka.com/uapi/open-banking/v1.0"

CHECK_INTERVAL = 300  # 5 minutes
_processed_transactions: set = set()


class TochkaBankClient:
    def __init__(self):
        self.jwt_token = TOCHKA_JWT_TOKEN
        self.account_id = TOCHKA_ACCOUNT_ID
        self.http_client = None
        self.db_pool = None

    async def init(self):
        self.http_client = httpx.AsyncClient(timeout=30)
        if POSTGRES_DSN:
            try:
                self.db_pool = await asyncpg.create_pool(
                    POSTGRES_DSN, min_size=1, max_size=3
                )
            except Exception as e:
                logger.error(f"Database connection failed: {e}")

    async def close(self):
        if self.http_client:
            await self.http_client.aclose()
        if self.db_pool:
            await self.db_pool.close()

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def create_statement(
        self, from_date: datetime, to_date: datetime
    ) -> Optional[str]:
        """Создать запрос на выписку и вернуть ID"""
        try:
            url = f"{TOCHKA_API_URL}/statements"
            data = {
                "Data": {
                    "Statement": {
                        "accountId": self.account_id,
                        "startDateTime": from_date.strftime("%Y-%m-%dT00:00:00+03:00"),
                        "endDateTime": to_date.strftime("%Y-%m-%dT00:00:00+03:00"),
                    }
                }
            }

            response = await self.http_client.post(
                url, headers=self._get_headers(), json=data
            )

            if response.status_code == 200:
                result = response.json()
                statement_id = (
                    result.get("Data", {}).get("Statement", {}).get("statementId")
                )
                logger.info(f"Statement created: {statement_id}")
                return statement_id
            else:
                logger.warning(
                    f"Failed to create statement: {response.status_code} - {response.text[:200]}"
                )
                return None

        except Exception as e:
            logger.error(f"Create statement error: {e}")
            return None

    async def get_statement(self, statement_id: str) -> List[Dict]:
        """Получить выписку по ID"""
        try:
            account_encoded = quote(self.account_id, safe="")
            url = (
                f"{TOCHKA_API_URL}/accounts/{account_encoded}/statements/{statement_id}"
            )

            response = await self.http_client.get(url, headers=self._get_headers())

            if response.status_code == 200:
                data = response.json()
                statements = data.get("Data", {}).get("Statement", [])
                if statements:
                    transactions = statements[0].get("Transaction", [])
                    logger.info(f"Got {len(transactions)} transactions")
                    return transactions
            else:
                logger.warning(f"Get statement failed: {response.status_code}")

            return []

        except Exception as e:
            logger.error(f"Get statement error: {e}")
            return []

    async def get_transactions(
        self, from_date: datetime, to_date: datetime
    ) -> List[Dict]:
        """Получить транзакции за период (через выписку)"""
        if not self.jwt_token or not self.account_id:
            logger.warning("Tochka credentials not configured")
            return []

        # Создаём выписку
        statement_id = await self.create_statement(from_date, to_date)
        if not statement_id:
            return []

        # Ждём готовности и получаем
        await asyncio.sleep(2)
        return await self.get_statement(statement_id)

    async def find_invoice_payments(self, transactions: List[Dict]) -> List[Dict]:
        """Найти платежи по нашим счетам"""
        matched = []

        for tx in transactions:
            # Только входящие (Credit)
            if tx.get("creditDebitIndicator") != "Credit":
                continue

            tx_id = tx.get("transactionId", "")
            if tx_id in _processed_transactions:
                continue

            amount_data = tx.get("amount", {})
            amount = float(amount_data.get("amount", 0))

            # Собираем информацию о платеже
            purpose = tx.get("remittanceInformation", {}).get("unstructured", "")
            payer = tx.get("debtorAccount", {}).get("name", "") or tx.get(
                "debtorAgent", {}
            ).get("name", "")

            # Ищем номер счёта SW-YYYY-NNNN
            match = re.search(r"SW-\d{4}-\d{4}", purpose, re.IGNORECASE)

            if match:
                invoice_number = match.group(0).upper()
                matched.append(
                    {
                        "transaction_id": tx_id,
                        "invoice_number": invoice_number,
                        "amount": amount,
                        "payer": payer,
                        "purpose": purpose,
                        "date": tx.get("bookingDateTime"),
                    }
                )
                logger.info(f"Found invoice payment: {invoice_number} for {amount}")

        return matched

    async def send_notification(self, message: str):
        if not SALES_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
            return
        try:
            await self.http_client.post(
                f"https://api.telegram.org/bot{SALES_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
        except Exception as e:
            logger.error(f"Telegram failed: {e}")

    async def process_payment(self, payment_info: Dict) -> bool:
        if not self.db_pool:
            logger.warning("No database connection")
            return False

        invoice_number = payment_info["invoice_number"]
        amount = payment_info["amount"]
        tx_id = payment_info["transaction_id"]

        async with self.db_pool.acquire() as conn:
            invoice = await conn.fetchrow(
                "SELECT * FROM widget_invoices WHERE invoice_number = $1 AND status = 'pending'",
                invoice_number,
            )

            if not invoice:
                logger.info(f"Invoice {invoice_number} not found or already paid")
                return False

            expected = float(invoice["amount"])
            if abs(amount - expected) > 1:
                await self.send_notification(
                    f"⚠️ <b>Сумма не совпадает!</b>\n"
                    f"Счёт: {invoice_number}\n"
                    f"Ожидали: {expected:.0f}₽ / Получили: {amount:.0f}₽"
                )
                return False

            license_data = await conn.fetchrow(
                "SELECT * FROM widget_licenses WHERE id = $1", invoice["license_id"]
            )
            if not license_data:
                return False

            plan = invoice["plan"]
            months = invoice.get("months", 1)

            # План и срок
            plan_days = {"ai_seller": 30, "head_of_sales": 30, "suite": 30}
            days = plan_days.get(plan, 30) * months

            now = datetime.now()
            expires = license_data["expires_at"]
            if expires and expires > now:
                expires_at = expires + timedelta(days=days)
            else:
                expires_at = now + timedelta(days=days)

            # Модули по плану
            modules = {
                "ai_seller": (True, False),
                "head_of_sales": (False, True),
                "suite": (True, True),
            }
            ai_seller, hos = modules.get(plan, (True, True))

            await conn.execute(
                """
                UPDATE widget_licenses SET plan=$1, status='active', expires_at=$2,
                module_ai_seller=$3, module_head_of_sales=$4, last_payment_at=NOW(), last_payment_amount=$5
                WHERE id=$6
            """,
                plan,
                expires_at,
                ai_seller,
                hos,
                amount,
                invoice["license_id"],
            )

            await conn.execute(
                "UPDATE widget_invoices SET status='paid', paid_at=NOW() WHERE id=$1",
                invoice["id"],
            )

            _processed_transactions.add(tx_id)

            key = license_data["license_key"]
            contact = invoice.get("telegram") or invoice.get("email") or "N/A"

            await self.send_notification(
                f"✅ <b>Оплата по счёту подтверждена!</b>\n\n"
                f"📄 {invoice_number}\n💰 {amount:,.0f}₽\n"
                f"🔑 <code>{key}</code>\n📅 До: {expires_at.strftime('%d.%m.%Y')}\n\n"
                f"📧 Переслать клиенту ({contact}):\n"
                f"Ваш ключ активирован: <code>{key}</code>"
            )
            return True

    async def check_payments(self):
        to_date = datetime.now()
        from_date = to_date - timedelta(days=3)

        transactions = await self.get_transactions(from_date, to_date)

        if transactions:
            matched = await self.find_invoice_payments(transactions)
            for p in matched:
                await self.process_payment(p)
        else:
            logger.debug("No transactions found")

    async def run_forever(self):
        await self.init()
        logger.info("Tochka Bank checker started")

        if not self.jwt_token:
            logger.error("TOCHKA_JWT_TOKEN not configured!")
            return

        if not self.account_id:
            logger.error("TOCHKA_ACCOUNT_ID not configured!")
            return

        await self.send_notification(
            "🏦 Мониторинг Точка Банк запущен\nСчёт: " + self.account_id[:20] + "..."
        )

        try:
            while True:
                try:
                    await self.check_payments()
                except Exception as e:
                    logger.error(f"Check error: {e}")
                await asyncio.sleep(CHECK_INTERVAL)
        finally:
            await self.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s"
    )
    client = TochkaBankClient()
    asyncio.run(client.run_forever())
