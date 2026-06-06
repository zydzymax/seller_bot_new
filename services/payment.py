"""
Sales Whisper Suite - Payment Service
YooKassa integration for subscription payments
"""

import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

import asyncpg
import httpx
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment", tags=["payment"])

# Configuration
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

SITE_URL = "https://saleswhisper.pro"

# Plans
PLANS = {
    "ai_seller": {"name": "AI Seller", "price": 1990},
    "head_of_sales": {"name": "Head of Sales", "price": 2990},
    "suite": {"name": "Suite", "price": 3990},
}

# Database pool
_db_pool = None


def _verify_admin_key(admin_key: Optional[str]) -> None:
    expected_key = os.getenv("ADMIN_API_KEY")
    if not expected_key:
        raise HTTPException(503, "ADMIN_API_KEY is not configured")
    if admin_key != expected_key:
        raise HTTPException(401, "Invalid admin key")


async def get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=10)
    return _db_pool


class PaymentRequest(BaseModel):
    plan: str
    price: int
    email: EmailStr
    telegram: Optional[str] = None
    domain: str
    company: Optional[str] = None


class PaymentResponse(BaseModel):
    payment_url: Optional[str] = None
    payment_id: Optional[str] = None
    error: Optional[str] = None


async def send_telegram_message(chat_id: str, message: str):
    """Send Telegram message"""
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception:
        logger.exception("Telegram send error")


async def create_yookassa_payment(
    amount: int, description: str, metadata: dict
) -> dict:
    """Create YooKassa payment"""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        raise HTTPException(500, "Payment system not configured")

    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": str(amount) + ".00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": f"{SITE_URL}/payment/success",
        },
        "capture": True,
        "description": description,
        "metadata": metadata,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json=payload,
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
            headers={"Idempotence-Key": idempotence_key},
            timeout=30,
        )

        if response.status_code != 200:
            logger.error(
                "YooKassa error status=%s body=%s",
                response.status_code,
                response.text,
            )
            raise HTTPException(500, "Payment creation failed")

        return response.json()


@router.post("/create", response_model=PaymentResponse)
async def create_payment(request: PaymentRequest):
    """Create payment and return redirect URL"""

    if request.plan not in PLANS:
        return PaymentResponse(error="Invalid plan")

    plan_info = PLANS[request.plan]

    # Validate price matches plan
    if request.price != plan_info["price"]:
        return PaymentResponse(error="Price mismatch")

    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Check or create license
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE account_domain = $1 OR email = $2",
            request.domain,
            request.email,
        )

        if not license_data:
            # Create new license entry (will be activated after payment)
            from services.licensing import generate_license_key

            license_key = generate_license_key()

            await conn.execute(
                """
                INSERT INTO widget_licenses (license_key, account_id, account_domain, email, company_name, plan, status)
                VALUES ($1, $2, $3, $4, $5, 'pending', 'pending')
            """,
                license_key,
                request.domain.split(".")[0],
                request.domain,
                request.email,
                request.company,
            )

            license_id = await conn.fetchval(
                "SELECT id FROM widget_licenses WHERE license_key = $1", license_key
            )
        else:
            license_id = license_data["id"]
            license_key = license_data["license_key"]

    # Create YooKassa payment
    try:
        metadata = {
            "license_id": str(license_id),
            "license_key": license_key,
            "plan": request.plan,
            "email": request.email,
            "telegram": request.telegram or "",
            "domain": request.domain,
        }

        payment = await create_yookassa_payment(
            amount=request.price,
            description=f"Sales Whisper {plan_info['name']} - 1 месяц",
            metadata=metadata,
        )

        # Save payment record
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO widget_payments (license_id, payment_id, amount, plan, status, metadata)
                VALUES ($1, $2, $3, $4, 'pending', $5)
            """,
                license_id,
                payment["id"],
                request.price,
                request.plan,
                metadata,
            )

        return PaymentResponse(
            payment_url=payment["confirmation"]["confirmation_url"],
            payment_id=payment["id"],
        )

    except Exception:
        logger.exception("Payment creation error")
        return PaymentResponse(error="Ошибка создания платежа")


@router.post("/webhook")
async def payment_webhook(request: Request):
    """Handle YooKassa webhook notifications"""

    try:
        data = await request.json()
        logger.info(
            "Payment webhook received event=%s object_id=%s",
            data.get("event"),
            data.get("object", {}).get("id"),
        )

        event = data.get("event")
        payment_obj = data.get("object", {})
        payment_id = payment_obj.get("id")
        status = payment_obj.get("status")
        metadata = payment_obj.get("metadata", {})

        if event != "payment.succeeded" or status != "succeeded":
            return {"status": "ignored"}

        pool = await get_db_pool()

        async with pool.acquire() as conn:
            # Update payment status
            await conn.execute(
                "UPDATE widget_payments SET status = 'succeeded', confirmed_at = NOW() WHERE payment_id = $1",
                payment_id,
            )

            # Activate license
            license_key = metadata.get("license_key")
            plan = metadata.get("plan")

            if license_key and plan:
                from services.licensing import PLANS

                plan_info = PLANS.get(plan, {})
                expires_at = datetime.now() + timedelta(days=plan_info.get("days", 30))

                await conn.execute(
                    """
                    UPDATE widget_licenses 
                    SET plan = $1, 
                        status = 'active',
                        expires_at = $2,
                        module_ai_seller = $3,
                        module_head_of_sales = $4,
                        last_payment_at = NOW(),
                        last_payment_amount = $5
                    WHERE license_key = $6
                """,
                    plan,
                    expires_at,
                    plan_info.get("ai_seller", True),
                    plan_info.get("head_of_sales", True),
                    float(payment_obj.get("amount", {}).get("value", 0)),
                    license_key,
                )

                # Send notifications
                email = metadata.get("email", "")
                telegram = metadata.get("telegram", "")
                domain = metadata.get("domain", "")
                amount = payment_obj.get("amount", {}).get("value", "0")

                # Notify admin
                admin_msg = f"""💰 <b>Успешная оплата!</b>

📋 Тариф: {plan_info.get('name', plan)}
💵 Сумма: {amount} ₽
📧 Email: {email}
💬 Telegram: {telegram or 'не указан'}
🔗 Домен: {domain}
🔑 Ключ: <code>{license_key}</code>
📅 До: {expires_at.strftime('%d.%m.%Y')}"""

                await send_telegram_message(TELEGRAM_ADMIN_CHAT_ID, admin_msg)

                # Send license key to customer via Telegram (if username provided)
                if telegram and telegram.startswith("@"):
                    # Note: Bot can only message users who started conversation first
                    # For now, we send to admin who can forward
                    customer_msg = f"""🎉 Спасибо за покупку Sales Whisper Suite!

Ваш лицензионный ключ:
<code>{license_key}</code>

Тариф: {plan_info.get('name', plan)}
Действует до: {expires_at.strftime('%d.%m.%Y')}

Введите ключ в настройках виджета в amoCRM.

По вопросам: @saleswhisper_support"""

                    # Send to admin with note to forward
                    await send_telegram_message(
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"📤 Переслать клиенту {telegram}:\n\n{customer_msg}",
                    )

        return {"status": "ok"}

    except Exception as e:
        logger.exception("Payment webhook error")
        return {"status": "error", "message": str(e)}


@router.get("/success")
async def payment_success():
    """Payment success redirect page"""
    return RedirectResponse(url=f"{SITE_URL}/payment-success.html")


# ============================================
# INVOICE PAYMENT (для юрлиц)
# ============================================


class InvoiceRequest(BaseModel):
    plan: str
    email: EmailStr
    telegram: Optional[str] = None
    domain: str
    company_name: str
    inn: str
    kpp: Optional[str] = None
    legal_address: Optional[str] = None
    months: int = 1


class InvoiceResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    amount: int
    pdf_url: Optional[str] = None
    message: str


@router.post("/invoice/create", response_model=InvoiceResponse)
async def create_invoice(request: InvoiceRequest):
    """Create invoice for legal entities"""

    if request.plan not in PLANS:
        raise HTTPException(400, "Invalid plan")

    plan_info = PLANS[request.plan]
    amount = plan_info["price"] * request.months

    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Create or get license
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE account_domain = $1 OR email = $2",
            request.domain,
            request.email,
        )

        if not license_data:
            from services.licensing import generate_license_key

            license_key = generate_license_key()

            await conn.execute(
                """
                INSERT INTO widget_licenses (license_key, account_id, account_domain, email, company_name, plan, status)
                VALUES ($1, $2, $3, $4, $5, 'pending', 'pending')
            """,
                license_key,
                request.domain.split(".")[0],
                request.domain,
                request.email,
                request.company_name,
            )

            license_id = await conn.fetchval(
                "SELECT id FROM widget_licenses WHERE license_key = $1", license_key
            )
        else:
            license_id = license_data["id"]
            license_key = license_data["license_key"]

        # Generate invoice number
        year = datetime.now().year
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM widget_invoices WHERE created_at >= $1",
            datetime(year, 1, 1),
        )
        invoice_number = f"SW-{year}-{(count or 0) + 1:04d}"

        # Create invoice record
        invoice_id = await conn.fetchval(
            """
            INSERT INTO widget_invoices 
            (license_id, invoice_number, amount, plan, months, company_name, inn, kpp, legal_address, email, telegram, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'pending')
            RETURNING id
        """,
            license_id,
            invoice_number,
            amount,
            request.plan,
            request.months,
            request.company_name,
            request.inn,
            request.kpp,
            request.legal_address,
            request.email,
            request.telegram,
        )

    # Notify admin
    notification = f"""📄 <b>Новый счёт!</b>

📋 Номер: {invoice_number}
💰 Сумма: {amount:,} ₽
📦 Тариф: {plan_info['name']} x {request.months} мес.

🏢 {request.company_name}
🔢 ИНН: {request.inn}
📧 {request.email}
💬 {request.telegram or 'не указан'}

Для подтверждения оплаты:
<code>/confirm {invoice_id}</code>"""

    await send_telegram_message(TELEGRAM_ADMIN_CHAT_ID, notification)

    return InvoiceResponse(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        amount=amount,
        message=f"Счёт {invoice_number} создан. Реквизиты отправлены на {request.email}",
    )


@router.post("/invoice/confirm/{invoice_id}")
async def confirm_invoice_payment(
    invoice_id: int, admin_key: str = Header(None, alias="X-Admin-Key")
):
    """Confirm invoice payment (admin only)"""
    _verify_admin_key(admin_key)

    pool = await get_db_pool()

    async with pool.acquire() as conn:
        invoice = await conn.fetchrow(
            "SELECT * FROM widget_invoices WHERE id = $1", invoice_id
        )

        if not invoice:
            raise HTTPException(404, "Invoice not found")

        if invoice["status"] == "paid":
            return {"message": "Already paid"}

        # Get license
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE id = $1", invoice["license_id"]
        )

        if not license_data:
            raise HTTPException(404, "License not found")

        # Activate license
        plan = invoice["plan"]
        months = invoice["months"]

        from services.licensing import PLANS

        plan_info = PLANS.get(plan, {})

        now = datetime.now()
        current_expires = license_data["expires_at"]

        if current_expires and current_expires > now:
            expires_at = current_expires + timedelta(
                days=plan_info.get("days", 30) * months
            )
        else:
            expires_at = now + timedelta(days=plan_info.get("days", 30) * months)

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
            expires_at,
            plan_info.get("ai_seller", True),
            plan_info.get("head_of_sales", True),
            invoice["amount"],
            invoice["license_id"],
        )

        # Update invoice
        await conn.execute(
            "UPDATE widget_invoices SET status = 'paid', paid_at = NOW() WHERE id = $1",
            invoice_id,
        )

        license_key = license_data["license_key"]

        # Notify
        notification = f"""✅ <b>Счёт оплачен!</b>

📋 {invoice['invoice_number']}
💰 {invoice['amount']:,} ₽
🏢 {invoice['company_name']}
🔑 Ключ: <code>{license_key}</code>
📅 До: {expires_at.strftime('%d.%m.%Y')}

Переслать клиенту ({invoice['telegram'] or invoice['email']}):
Ваш ключ: <code>{license_key}</code>"""

        await send_telegram_message(TELEGRAM_ADMIN_CHAT_ID, notification)

        return {
            "success": True,
            "license_key": license_key,
            "expires_at": expires_at.isoformat(),
        }
