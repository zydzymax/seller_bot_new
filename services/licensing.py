"""
Sales Whisper Suite - Licensing Service
Handles license management, verification, and notifications
"""

import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

import asyncpg
import httpx
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["licensing"])

# Configuration
POSTGRES_DSN = os.getenv('POSTGRES_DSN', 'postgresql://sovani:[REVOKED_SECRET_REMOVED]@127.0.0.1:5433/sovani_ai_seller')
TELEGRAM_BOT_TOKEN = os.getenv('SALES_BOT_TOKEN', '')
TELEGRAM_ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_CHAT_ID', '')  # Твой chat_id для уведомлений

TRIAL_DAYS = 7

# Тарифы
PLANS = {
    'trial': {'name': 'Пробный период', 'price': 0, 'ai_seller': True, 'head_of_sales': True, 'days': 7},
    'ai_seller': {'name': 'AI Seller', 'price': 1990, 'ai_seller': True, 'head_of_sales': False, 'days': 30},
    'head_of_sales': {'name': 'Head of Sales', 'price': 2990, 'ai_seller': False, 'head_of_sales': True, 'days': 30},
    'suite': {'name': 'Suite (всё включено)', 'price': 3990, 'ai_seller': True, 'head_of_sales': True, 'days': 30},
}


class Plan(str, Enum):
    TRIAL = 'trial'
    AI_SELLER = 'ai_seller'
    HEAD_OF_SALES = 'head_of_sales'
    SUITE = 'suite'


class LicenseStatus(str, Enum):
    ACTIVE = 'active'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'


class LicenseRequest(BaseModel):
    account_id: str
    account_domain: Optional[str] = None
    email: EmailStr
    company_name: Optional[str] = None


class LicenseResponse(BaseModel):
    license_key: str
    plan: str
    status: str
    module_ai_seller: bool
    module_head_of_sales: bool
    trial_ends_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    days_left: int = 0


class LicenseVerifyResponse(BaseModel):
    valid: bool
    plan: str = 'none'
    module_ai_seller: bool = False
    module_head_of_sales: bool = False
    days_left: int = 0
    message: str = ''


# Database connection pool
_db_pool = None

async def get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=10)
    return _db_pool


def generate_license_key() -> str:
    """Generate unique license key"""
    random_part = secrets.token_hex(12)
    timestamp = hex(int(datetime.now().timestamp()))[2:]
    key = f"SW-{random_part[:8].upper()}-{random_part[8:16].upper()}-{timestamp.upper()}"
    return key


async def send_telegram_notification(message: str):
    """Send notification to admin Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
        logger.info("Telegram notification sent")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


@router.post("/register", response_model=LicenseResponse)
async def register_license(request: LicenseRequest):
    """
    Register new license (trial)
    Called when widget is installed
    """
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        # Check if license already exists for this account
        existing = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE account_id = $1",
            request.account_id
        )
        
        if existing:
            # Return existing license
            days_left = 0
            if existing['expires_at']:
                days_left = max(0, (existing['expires_at'] - datetime.now()).days)
            elif existing['trial_ends_at']:
                days_left = max(0, (existing['trial_ends_at'] - datetime.now()).days)
            
            return LicenseResponse(
                license_key=existing['license_key'],
                plan=existing['plan'],
                status=existing['status'],
                module_ai_seller=existing['module_ai_seller'],
                module_head_of_sales=existing['module_head_of_sales'],
                trial_ends_at=existing['trial_ends_at'],
                expires_at=existing['expires_at'],
                days_left=days_left
            )
        
        # Create new trial license
        license_key = generate_license_key()
        trial_ends = datetime.now() + timedelta(days=TRIAL_DAYS)
        
        await conn.execute("""
            INSERT INTO widget_licenses 
            (license_key, account_id, account_domain, email, company_name, plan, trial_ends_at, module_ai_seller, module_head_of_sales)
            VALUES ($1, $2, $3, $4, $5, 'trial', $6, true, true)
        """, license_key, request.account_id, request.account_domain, request.email, request.company_name, trial_ends)
        
        # Send Telegram notification
        notification = f"""🆕 <b>Новая регистрация Sales Whisper Suite!</b>

📧 Email: {request.email}
🏢 Компания: {request.company_name or 'Не указана'}
🔗 Домен: {request.account_domain or 'Не указан'}
🆔 Account ID: {request.account_id}
🔑 Лицензия: <code>{license_key}</code>
📅 Триал до: {trial_ends.strftime('%d.%m.%Y')}"""
        
        await send_telegram_notification(notification)
        
        return LicenseResponse(
            license_key=license_key,
            plan='trial',
            status='active',
            module_ai_seller=True,
            module_head_of_sales=True,
            trial_ends_at=trial_ends,
            days_left=TRIAL_DAYS
        )


@router.get("/verify/{license_key}", response_model=LicenseVerifyResponse)
async def verify_license(license_key: str):
    """Verify license and return available modules"""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE license_key = $1",
            license_key
        )
        
        if not license_data:
            return LicenseVerifyResponse(
                valid=False,
                message="Лицензия не найдена"
            )
        
        # Check status
        if license_data['status'] == 'cancelled':
            return LicenseVerifyResponse(
                valid=False,
                message="Лицензия отменена"
            )
        
        # Check expiration
        now = datetime.now()
        expires_at = license_data['expires_at'] or license_data['trial_ends_at']
        
        if expires_at and now > expires_at:
            # Update status to expired
            await conn.execute(
                "UPDATE widget_licenses SET status = 'expired' WHERE license_key = $1",
                license_key
            )
            return LicenseVerifyResponse(
                valid=False,
                plan=license_data['plan'],
                message="Срок действия лицензии истёк. Продлите подписку."
            )
        
        days_left = max(0, (expires_at - now).days) if expires_at else 0
        
        return LicenseVerifyResponse(
            valid=True,
            plan=license_data['plan'],
            module_ai_seller=license_data['module_ai_seller'],
            module_head_of_sales=license_data['module_head_of_sales'],
            days_left=days_left,
            message="Лицензия активна"
        )


@router.get("/verify-by-account/{account_id}", response_model=LicenseVerifyResponse)
async def verify_by_account(account_id: str):
    """Verify license by AmoCRM account ID"""
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE account_id = $1",
            account_id
        )
        
        if not license_data:
            return LicenseVerifyResponse(
                valid=False,
                message="Лицензия не найдена. Установите виджет заново."
            )
        
        # Delegate to main verify function
        return await verify_license(license_data['license_key'])


@router.get("/plans")
async def get_plans():
    """Get available plans and pricing"""
    return {
        "plans": [
            {"id": k, **v} for k, v in PLANS.items()
        ],
        "currency": "RUB",
        "trial_days": TRIAL_DAYS
    }


@router.post("/activate")
async def activate_plan(
    license_key: str,
    plan: Plan,
    months: int = 1
):
    """
    Activate paid plan (called after successful payment)
    """
    if plan == Plan.TRIAL:
        raise HTTPException(400, "Cannot activate trial plan")
    
    pool = await get_db_pool()
    plan_info = PLANS[plan.value]
    
    async with pool.acquire() as conn:
        license_data = await conn.fetchrow(
            "SELECT * FROM widget_licenses WHERE license_key = $1",
            license_key
        )
        
        if not license_data:
            raise HTTPException(404, "License not found")
        
        # Calculate new expiration
        now = datetime.now()
        current_expires = license_data['expires_at']
        
        if current_expires and current_expires > now:
            # Extend from current expiration
            new_expires = current_expires + timedelta(days=plan_info['days'] * months)
        else:
            # Start fresh
            new_expires = now + timedelta(days=plan_info['days'] * months)
        
        # Update license
        await conn.execute("""
            UPDATE widget_licenses 
            SET plan = $1, 
                expires_at = $2, 
                status = 'active',
                module_ai_seller = $3,
                module_head_of_sales = $4,
                last_payment_at = $5,
                last_payment_amount = $6
            WHERE license_key = $7
        """, 
            plan.value,
            new_expires,
            plan_info['ai_seller'],
            plan_info['head_of_sales'],
            now,
            plan_info['price'] * months,
            license_key
        )
        
        # Send notification
        notification = f"""💰 <b>Новая оплата Sales Whisper Suite!</b>

🔑 Лицензия: <code>{license_key}</code>
📋 Тариф: {plan_info['name']}
💵 Сумма: {plan_info['price'] * months} ₽
📅 Период: {months} мес.
⏰ Действует до: {new_expires.strftime('%d.%m.%Y')}"""
        
        await send_telegram_notification(notification)
        
        return {
            "success": True,
            "plan": plan.value,
            "expires_at": new_expires.isoformat(),
            "message": f"Тариф {plan_info['name']} активирован до {new_expires.strftime('%d.%m.%Y')}"
        }


@router.get("/admin/all")
async def admin_list_licenses(api_key: str = Header(None, alias="X-Admin-Key")):
    """Admin: List all licenses"""
    expected_key = os.getenv('ADMIN_API_KEY', 'saleswhisper_admin_2025')
    if api_key != expected_key:
        raise HTTPException(401, "Invalid admin key")
    
    pool = await get_db_pool()
    
    async with pool.acquire() as conn:
        licenses = await conn.fetch("SELECT * FROM widget_licenses ORDER BY created_at DESC LIMIT 100")
        
        return {
            "count": len(licenses),
            "licenses": [dict(row) for row in licenses]
        }
