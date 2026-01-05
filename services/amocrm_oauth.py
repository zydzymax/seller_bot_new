"""
AmoCRM/Kommo OAuth 2.0 Integration for Marketplace Widget
Sales Whisper Manager - AI Sales Assistant

Handles:
- Widget installation flow
- OAuth token exchange
- Token refresh
- Webhook events (install/uninstall)
"""

import os
import hmac
import hashlib
import time
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException, Query, Header, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/amocrm", tags=["amocrm-oauth"])

# ============ Configuration ============

AMOCRM_CLIENT_ID = os.getenv('AMOCRM_CLIENT_ID', '')
AMOCRM_CLIENT_SECRET = os.getenv('AMOCRM_CLIENT_SECRET', '')
AMOCRM_REDIRECT_URI = os.getenv('AMOCRM_REDIRECT_URI', 'https://saleswhisper.pro/api/amocrm/callback')
WIDGET_SECRET_KEY = os.getenv('AMOCRM_WIDGET_SECRET', '')

# Token storage (in production use Redis/PostgreSQL)
# Format: {account_id: {access_token, refresh_token, expires_at, domain}}
_token_storage: Dict[str, Dict[str, Any]] = {}


# ============ Models ============

class TokenData(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: datetime
    domain: str
    account_id: str


class WebhookPayload(BaseModel):
    account_id: str
    timestamp: int
    hash: str
    data: Optional[Dict[str, Any]] = None


class InstallationResult(BaseModel):
    success: bool
    account_id: Optional[str] = None
    message: str


# ============ Token Storage Interface ============

class TokenStorage:
    """
    Token storage interface. 
    Override this class for Redis/PostgreSQL in production.
    """
    
    @staticmethod
    async def save_tokens(account_id: str, tokens: Dict[str, Any]) -> None:
        """Save tokens for account"""
        _token_storage[account_id] = {
            'access_token': tokens['access_token'],
            'refresh_token': tokens['refresh_token'],
            'expires_at': datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 86400)),
            'domain': tokens.get('domain', ''),
            'account_id': account_id,
            'created_at': datetime.utcnow().isoformat()
        }
        logger.info(f"Tokens saved for account {account_id}")
    
    @staticmethod
    async def get_tokens(account_id: str) -> Optional[Dict[str, Any]]:
        """Get tokens for account"""
        return _token_storage.get(account_id)
    
    @staticmethod
    async def delete_tokens(account_id: str) -> None:
        """Delete tokens for account (on uninstall)"""
        if account_id in _token_storage:
            del _token_storage[account_id]
            logger.info(f"Tokens deleted for account {account_id}")
    
    @staticmethod
    async def list_accounts() -> list:
        """List all installed accounts"""
        return list(_token_storage.keys())


token_storage = TokenStorage()


# ============ OAuth Helpers ============

def verify_webhook_signature(account_id: str, timestamp: int, received_hash: str) -> bool:
    """Verify webhook signature from AmoCRM"""
    if not WIDGET_SECRET_KEY:
        logger.warning("Widget secret key not configured")
        return True  # Skip verification in dev
    
    # Check timestamp freshness (5 min window)
    if abs(time.time() - timestamp) > 300:
        return False
    
    # Calculate expected hash
    check_string = f"{account_id}{timestamp}{WIDGET_SECRET_KEY}"
    expected_hash = hashlib.sha256(check_string.encode()).hexdigest()
    
    return hmac.compare_digest(expected_hash, received_hash)


async def exchange_code_for_tokens(code: str, domain: str) -> Dict[str, Any]:
    """Exchange authorization code for access/refresh tokens"""
    token_url = f"https://{domain}/oauth2/access_token"
    
    payload = {
        'client_id': AMOCRM_CLIENT_ID,
        'client_secret': AMOCRM_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': AMOCRM_REDIRECT_URI
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Token exchange failed: {response.text}")
            raise HTTPException(status_code=400, detail="Token exchange failed")
        
        return response.json()


async def refresh_access_token(account_id: str) -> Optional[Dict[str, Any]]:
    """Refresh expired access token"""
    tokens = await token_storage.get_tokens(account_id)
    if not tokens:
        return None
    
    domain = tokens['domain']
    token_url = f"https://{domain}/oauth2/access_token"
    
    payload = {
        'client_id': AMOCRM_CLIENT_ID,
        'client_secret': AMOCRM_CLIENT_SECRET,
        'grant_type': 'refresh_token',
        'refresh_token': tokens['refresh_token'],
        'redirect_uri': AMOCRM_REDIRECT_URI
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Token refresh failed for {account_id}: {response.text}")
            return None
        
        new_tokens = response.json()
        new_tokens['domain'] = domain
        await token_storage.save_tokens(account_id, new_tokens)
        
        return new_tokens


async def get_valid_token(account_id: str) -> Optional[str]:
    """Get valid access token, refresh if needed"""
    tokens = await token_storage.get_tokens(account_id)
    if not tokens:
        return None
    
    # Check if token expired
    if datetime.utcnow() >= tokens['expires_at']:
        new_tokens = await refresh_access_token(account_id)
        if not new_tokens:
            return None
        return new_tokens['access_token']
    
    return tokens['access_token']


# ============ OAuth Endpoints ============

@router.get("/install")
async def oauth_install(
    client_id: str = Query(...),
    state: str = Query(...),
    from_widget: str = Query(default="")
):
    """
    Step 1: User clicks 'Install' in AmoCRM marketplace
    Redirect to AmoCRM authorization page
    """
    if client_id != AMOCRM_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    # Build authorization URL
    auth_params = {
        'client_id': AMOCRM_CLIENT_ID,
        'state': state,
        'mode': 'post_message' if from_widget else 'popup'
    }
    
    # AmoCRM will redirect back to our callback
    auth_url = f"https://www.amocrm.ru/oauth?{urlencode(auth_params)}"
    
    logger.info(f"OAuth install initiated, redirecting to AmoCRM")
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    code: str = Query(default=None),
    state: str = Query(default=None),
    referer: str = Query(default=None),
    platform: str = Query(default="amocrm"),
    client_id: str = Query(default=None),
    error: str = Query(default=None),
    error_description: str = Query(default=None)
):
    """
    Step 2: AmoCRM redirects back with authorization code
    Exchange code for tokens and complete installation
    """
    if error:
        logger.error(f"OAuth error: {error} - {error_description}")
        return HTMLResponse(content=f'''
            <html>
            <head><title>Ошибка установки</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1>❌ Ошибка установки</h1>
                <p>{error_description or error}</p>
                <p><a href="https://saleswhisper.pro">Вернуться на сайт</a></p>
            </body>
            </html>
        ''', status_code=400)
    
    if not code or not referer:
        raise HTTPException(status_code=400, detail="Missing code or referer")
    
    # Extract domain from referer
    # referer format: https://subdomain.amocrm.ru/...
    try:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        domain = parsed.netloc  # subdomain.amocrm.ru
    except Exception as e:
        logger.error(f"Failed to parse referer: {e}")
        raise HTTPException(status_code=400, detail="Invalid referer")
    
    # Exchange code for tokens
    try:
        tokens = await exchange_code_for_tokens(code, domain)
        tokens['domain'] = domain
        
        # Get account info
        account_id = str(tokens.get('account_id', ''))
        if not account_id:
            # Fetch account info from API
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://{domain}/api/v4/account",
                    headers={'Authorization': f"Bearer {tokens['access_token']}"}
                )
                if resp.status_code == 200:
                    account_id = str(resp.json().get('id', ''))
        
        # Save tokens
        await token_storage.save_tokens(account_id, tokens)
        
        logger.info(f"Widget installed for account {account_id} ({domain})")
        
        # Success page
        return HTMLResponse(content=f'''
            <html>
            <head>
                <title>Установка завершена</title>
                <script>
                    // Notify parent window (AmoCRM) about successful installation
                    if (window.opener) {{
                        window.opener.postMessage({{
                            type: 'oauth',
                            status: 'success',
                            account_id: '{account_id}'
                        }}, '*');
                        window.close();
                    }}
                </script>
            </head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1>✅ Sales Whisper Manager установлен!</h1>
                <p>Виджет успешно подключен к вашему аккаунту AmoCRM.</p>
                <p>Теперь вы можете закрыть это окно и использовать виджет в карточках сделок.</p>
                <p style="margin-top: 30px;">
                    <a href="https://{domain}" style="padding: 10px 20px; background: #4c8bf5; color: white; text-decoration: none; border-radius: 5px;">
                        Перейти в AmoCRM
                    </a>
                </p>
            </body>
            </html>
        ''')
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return HTMLResponse(content=f'''
            <html>
            <head><title>Ошибка</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1>❌ Ошибка установки</h1>
                <p>Не удалось завершить установку. Попробуйте еще раз.</p>
                <p style="color: #999; font-size: 12px;">{str(e)}</p>
            </body>
            </html>
        ''', status_code=500)


@router.post("/webhook")
async def webhook_handler(request: Request):
    """
    Webhook endpoint for AmoCRM events:
    - Widget installation
    - Widget uninstallation  
    - Settings update
    """
    try:
        body = await request.json()
    except:
        body = {}
    
    # Get headers
    account_id = request.headers.get('X-Account-Id', body.get('account_id', ''))
    timestamp = request.headers.get('X-Timestamp', body.get('timestamp', 0))
    signature = request.headers.get('X-Signature', body.get('hash', ''))
    
    # Verify signature
    if not verify_webhook_signature(account_id, int(timestamp), signature):
        logger.warning(f"Invalid webhook signature for account {account_id}")
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    event_type = body.get('event', '')
    
    if event_type == 'install':
        logger.info(f"Widget installed via webhook for account {account_id}")
        return {"status": "ok", "message": "Installation confirmed"}
    
    elif event_type == 'uninstall':
        await token_storage.delete_tokens(account_id)
        logger.info(f"Widget uninstalled for account {account_id}")
        return {"status": "ok", "message": "Uninstallation confirmed"}
    
    elif event_type == 'settings_update':
        logger.info(f"Settings updated for account {account_id}")
        return {"status": "ok", "message": "Settings updated"}
    
    else:
        logger.info(f"Unknown webhook event: {event_type}")
        return {"status": "ok"}


@router.delete("/uninstall/{account_id}")
async def manual_uninstall(account_id: str):
    """Manual uninstall endpoint"""
    await token_storage.delete_tokens(account_id)
    return {"status": "ok", "message": f"Account {account_id} uninstalled"}


# ============ API Helpers for Widget ============

@router.get("/accounts")
async def list_installed_accounts():
    """List all accounts with installed widget (admin endpoint)"""
    accounts = await token_storage.list_accounts()
    return {"accounts": accounts, "count": len(accounts)}


@router.get("/account/{account_id}/status")
async def get_account_status(account_id: str):
    """Check if account has valid token"""
    tokens = await token_storage.get_tokens(account_id)
    if not tokens:
        return {"installed": False}
    
    return {
        "installed": True,
        "domain": tokens.get('domain', ''),
        "expires_at": tokens.get('expires_at', '').isoformat() if tokens.get('expires_at') else None
    }


async def get_amocrm_client(account_id: str) -> Optional[httpx.AsyncClient]:
    """Get authenticated HTTP client for AmoCRM API calls"""
    token = await get_valid_token(account_id)
    if not token:
        return None
    
    tokens = await token_storage.get_tokens(account_id)
    domain = tokens.get('domain', '')
    
    return httpx.AsyncClient(
        base_url=f"https://{domain}",
        headers={'Authorization': f"Bearer {token}"},
        timeout=30.0
    )
