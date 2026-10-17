from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import Settings


def user_client(settings: Settings) -> TelegramClient:
    if settings.session:
        session: StringSession | str = StringSession(settings.session)
    else:
        settings.session_path.parent.mkdir(parents=True, exist_ok=True)
        session = str(settings.session_path)
    return TelegramClient(
        session,
        settings.api_id,
        settings.api_hash,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8 x64",
        lang_code="en",
        system_lang_code="en-US",
    )
