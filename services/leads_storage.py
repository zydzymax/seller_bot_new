"""
Leads Storage Service — Единое хранилище заявок

Сохраняет заявки из всех источников:
- Форма на сайте
- Веб-чат виджет
- Telegram бот

Данные хранятся в Redis и дублируются в JSON файл для надёжности.
+ Отправка уведомлений в Telegram
+ Сохранение в Google Sheets
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path

import redis.asyncio as aioredis
from telegram import Bot
from utils.logging import get_logger

logger = get_logger(__name__)

# Путь к файлу с заявками
LEADS_FILE = os.getenv("LEADS_FILE", "/app/data/leads.json")
LEADS_REDIS_KEY = "saleswhisper:leads"
LEADS_COUNTER_KEY = "saleswhisper:leads:counter"

# Telegram уведомления
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "769582971"))

# Google Sheets (опционально)
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
GOOGLE_SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID")


async def send_telegram_notification(lead: "Lead") -> bool:
    """
    Отправить уведомление о новом лиде в Telegram.

    Args:
        lead: Объект заявки

    Returns:
        True если уведомление отправлено успешно
    """
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN not set, skipping notification")
        return False

    try:
        bot = Bot(token=TELEGRAM_TOKEN)

        # Формируем текст уведомления
        source_emoji = {"website_form": "🌐", "web_chat": "💬", "telegram": "📱"}.get(
            lead.source, "📋"
        )

        text = f"""
{source_emoji} <b>Новый лид #{lead.id}</b>

👤 <b>Имя:</b> {lead.name or 'Не указано'}
📞 <b>Контакт:</b> {lead.contact}
📂 <b>Источник:</b> {lead.source}
"""

        if lead.niche:
            text += f"🏢 <b>Ниша:</b> {lead.niche}\n"

        if lead.leads_per_month:
            text += f"📊 <b>Заявок/мес:</b> {lead.leads_per_month}\n"

        if lead.pain:
            text += f"🎯 <b>Боль:</b> {lead.pain}\n"

        if lead.message:
            text += f"💭 <b>Сообщение:</b> {lead.message[:200]}{'...' if len(lead.message) > 200 else ''}\n"

        text += f"\n🕐 {lead.created_at}"

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="HTML")

        logger.info(f"Telegram notification sent for lead #{lead.id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False


async def save_to_google_sheets(lead: "Lead") -> bool:
    """
    Сохранить лид в Google Sheets.

    Требует:
    - GOOGLE_SHEETS_CREDENTIALS: путь к JSON файлу с Service Account credentials
    - GOOGLE_SPREADSHEET_ID: ID таблицы

    Args:
        lead: Объект заявки

    Returns:
        True если сохранено успешно
    """
    if not GOOGLE_SHEETS_CREDENTIALS or not GOOGLE_SPREADSHEET_ID:
        logger.debug("Google Sheets not configured, skipping")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        # Авторизация
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(
            GOOGLE_SHEETS_CREDENTIALS, scopes=scopes
        )
        gc = gspread.authorize(creds)

        # Открываем таблицу
        spreadsheet = gc.open_by_key(GOOGLE_SPREADSHEET_ID)
        worksheet = spreadsheet.sheet1  # Первый лист

        # Добавляем строку с данными лида
        row = [
            lead.id,
            lead.created_at,
            lead.source,
            lead.name or "",
            lead.contact,
            lead.contact_type,
            lead.niche or "",
            lead.leads_per_month or "",
            lead.pain or "",
            lead.message or "",
            lead.session_id or "",
            lead.conversation_id or "",
        ]

        # Находим следующую пустую строку (по первой колонке)
        col_a_values = worksheet.col_values(1)
        next_row = len(col_a_values) + 1

        # Вставляем данные в конкретную строку
        worksheet.insert_row(row, next_row, value_input_option="USER_ENTERED")

        logger.info(f"Lead #{lead.id} saved to Google Sheets")
        return True

    except ImportError:
        logger.warning("gspread not installed. Run: pip install gspread google-auth")
        return False
    except Exception as e:
        logger.error(f"Failed to save to Google Sheets: {e}")
        return False


@dataclass
class Lead:
    """Структура заявки"""

    id: int
    source: str  # "website_form", "web_chat", "telegram"
    name: str
    contact: str  # телефон, email, telegram username
    contact_type: str  # "phone", "email", "telegram"

    # Дополнительные данные
    niche: Optional[str] = None
    leads_per_month: Optional[int] = None
    pain: Optional[str] = None
    message: Optional[str] = None

    # Мета-данные
    created_at: str = ""
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class LeadsStorage:
    """Сервис хранения заявок"""

    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self.redis = redis_client
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """Создать директорию для данных если её нет"""
        data_dir = Path(LEADS_FILE).parent
        data_dir.mkdir(parents=True, exist_ok=True)

        # Создать пустой файл если не существует
        if not Path(LEADS_FILE).exists():
            with open(LEADS_FILE, "w") as f:
                json.dump([], f)

    async def _get_next_id(self) -> int:
        """Получить следующий ID заявки"""
        if self.redis:
            return await self.redis.incr(LEADS_COUNTER_KEY)
        else:
            # Fallback на файл
            leads = self._read_leads_file()
            return max([lead_item.get("id", 0) for lead_item in leads], default=0) + 1

    def _read_leads_file(self) -> List[Dict[str, Any]]:
        """Прочитать заявки из файла"""
        try:
            with open(LEADS_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_leads_file(self, leads: List[Dict[str, Any]]):
        """Записать заявки в файл"""
        with open(LEADS_FILE, "w") as f:
            json.dump(leads, f, ensure_ascii=False, indent=2)

    async def save_lead(self, lead: Lead) -> Dict[str, Any]:
        """
        Сохранить заявку.

        Args:
            lead: Объект заявки

        Returns:
            Dict с результатом операции
        """
        try:
            # Получаем ID если не задан
            if not lead.id:
                lead.id = await self._get_next_id()

            lead_dict = asdict(lead)

            # Сохраняем в Redis
            if self.redis:
                await self.redis.lpush(
                    LEADS_REDIS_KEY, json.dumps(lead_dict, ensure_ascii=False)
                )
                # Сохраняем отдельно по ID для быстрого доступа
                await self.redis.set(
                    f"{LEADS_REDIS_KEY}:{lead.id}",
                    json.dumps(lead_dict, ensure_ascii=False),
                )

            # Дублируем в файл
            leads = self._read_leads_file()
            leads.append(lead_dict)
            self._write_leads_file(leads)

            logger.info(
                "Lead saved",
                lead_id=lead.id,
                source=lead.source,
                contact=(
                    lead.contact[:20] + "..."
                    if len(lead.contact) > 20
                    else lead.contact
                ),
            )

            # Отправляем уведомление в Telegram (без await чтобы не блокировать)
            asyncio.create_task(send_telegram_notification(lead))

            # Сохраняем в Google Sheets (если настроено)
            asyncio.create_task(save_to_google_sheets(lead))

            return {
                "status": "success",
                "lead_id": lead.id,
                "created_at": lead.created_at,
            }

        except Exception as e:
            logger.error(f"Error saving lead: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def get_leads(
        self, source: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список заявок.

        Args:
            source: Фильтр по источнику
            limit: Максимальное количество
            offset: Смещение

        Returns:
            Список заявок
        """
        try:
            if self.redis:
                # Из Redis
                raw_leads = await self.redis.lrange(
                    LEADS_REDIS_KEY, offset, offset + limit - 1
                )
                leads = [json.loads(raw_lead) for raw_lead in raw_leads]
            else:
                # Из файла
                all_leads = self._read_leads_file()
                leads = all_leads[offset : offset + limit]

            # Фильтрация по источнику
            if source:
                leads = [
                    lead_item for lead_item in leads if lead_item.get("source") == source
                ]

            return leads

        except Exception as e:
            logger.error(f"Error getting leads: {e}")
            return []

    async def get_lead_by_id(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Получить заявку по ID"""
        try:
            if self.redis:
                raw = await self.redis.get(f"{LEADS_REDIS_KEY}:{lead_id}")
                if raw:
                    return json.loads(raw)

            # Fallback на файл
            leads = self._read_leads_file()
            for lead in leads:
                if lead.get("id") == lead_id:
                    return lead

            return None

        except Exception as e:
            logger.error(f"Error getting lead {lead_id}: {e}")
            return None

    async def get_stats(self) -> Dict[str, Any]:
        """Получить статистику по заявкам"""
        try:
            leads = self._read_leads_file()

            stats = {"total": len(leads), "by_source": {}, "today": 0, "this_week": 0}

            today = datetime.now().date()
            datetime.now().date().isoformat()

            for lead in leads:
                # По источникам
                source = lead.get("source", "unknown")
                stats["by_source"][source] = stats["by_source"].get(source, 0) + 1

                # За сегодня
                created = lead.get("created_at", "")
                if created.startswith(today.isoformat()):
                    stats["today"] += 1

            return stats

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}


# Singleton
_leads_storage: Optional[LeadsStorage] = None


async def get_leads_storage() -> LeadsStorage:
    """Получить singleton экземпляр LeadsStorage"""
    global _leads_storage

    if _leads_storage is None:
        # Подключение к Redis
        redis_url = os.getenv("REDIS_ADDR", "redis://localhost:6379/0")
        try:
            redis_client = aioredis.from_url(redis_url)
            await redis_client.ping()
        except Exception as e:
            logger.warning(f"Redis not available: {e}, using file-only mode")
            redis_client = None

        _leads_storage = LeadsStorage(redis_client)
        logger.info("LeadsStorage initialized")

    return _leads_storage


def detect_contact_type(contact: str) -> str:
    """Определить тип контакта"""
    contact = contact.strip()

    # Telegram username
    if contact.startswith("@"):
        return "telegram"

    # Email
    if "@" in contact and "." in contact:
        return "email"

    # Телефон (цифры, пробелы, дефисы, скобки, плюс)
    import re

    phone_pattern = r"^[\+]?[\d\s\-\(\)]{7,20}$"
    if re.match(phone_pattern, contact.replace(" ", "")):
        return "phone"

    # По умолчанию - telegram
    return "telegram"
