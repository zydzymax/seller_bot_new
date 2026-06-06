import httpx
import os
import json

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


async def send_chat_action(chat_id: int, action: str = "typing"):
    if not BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
                data={"chat_id": chat_id, "action": action},
            )
    except Exception:
        pass  # Silent fail


async def send_message(chat_id: int, text: str, keyboard=None, inline=False):
    if not BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        if inline:
            payload["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [{"text": b, "callback_data": b} for b in row]
                        for row in keyboard
                    ]
                }
            )
        else:
            payload["reply_markup"] = json.dumps(
                {
                    "keyboard": keyboard,
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                }
            )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=payload
            )
    except Exception:
        pass  # Silent fail
