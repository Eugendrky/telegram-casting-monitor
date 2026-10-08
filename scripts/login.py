"""Один раз локально: печатает TELEGRAM_SESSION для Railway."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def login() -> None:
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        sys.exit("Сначала положите TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")

    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = await client.get_me()
        session = client.session.save()
        print()
        print(f"Вошли как: {getattr(me, 'username', None) or me.id}")
        print("Скопируйте в Railway / .env как TELEGRAM_SESSION:")
        print()
        print(session)
        print()


if __name__ == "__main__":
    asyncio.run(login())
