"""Telegram trigger-alert delivery -- mirrors
engine/src/api/swing.rs::send_telegram_message.
"""

from __future__ import annotations

import httpx


async def send_telegram_message(
    client: httpx.AsyncClient, token: str, chat_id: str, text: str
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = await client.post(
        url,
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"telegram sendMessage returned {response.status_code}")
