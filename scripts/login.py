"""Локальный вход в ваш Telegram без регистрации приложения на my.telegram.org."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app.client import user_client  # noqa: E402
from app.config import load_settings  # noqa: E402


async def login() -> None:
    os.chdir(ROOT)
    settings = load_settings()
    client = user_client(settings)
    await client.start()
    me = await client.get_me()
    print()
    print(f"Вошли как: {getattr(me, 'username', None) or me.id}")
    if settings.used_official_api:
        print("Ключи приложения: официальный Telegram Android (my.telegram.org не нужен).")
    if settings.session:
        print("Использована TELEGRAM_SESSION из .env")
    else:
        print(f"Сессия сохранена в файл: {settings.session_path}")
        print("Дальше достаточно: python -m app.main")
    saved = StringSession.save(client.session)
    if saved:
        print()
        print("Строка сессии (нужна только если позже понесёте это на сервер):")
        print(saved)
    print()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())
