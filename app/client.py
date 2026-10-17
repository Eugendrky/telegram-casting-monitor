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
        device_model="Casting Monitor",
        app_version="1.0",
    )
