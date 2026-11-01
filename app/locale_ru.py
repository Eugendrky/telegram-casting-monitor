"""User-visible Russian labels for casting monitor."""
from __future__ import annotations

from datetime import datetime, timezone

SUGGESTION_STATES = {
    "pending": "ожидает проверки",
    "approved": "одобрен",
    "rejected": "отклонён",
}

def suggestion_status(value: str) -> str:
    return SUGGESTION_STATES.get(value, "статус неизвестен")

def readable_time(value: int | None) -> str:
    if not value:
        return "ещё не проверялся"
    try:
        return datetime.fromtimestamp(int(value),timezone.utc).strftime("%d.%m.%Y %H:%M (UTC)")
    except (ValueError,TypeError,OverflowError,OSError):
        return "время неизвестно"

def source_error(value: str | None) -> str:
    if not value:
        return "нет"
    if value in ("rate_limited","public preview rate limited"):
        return "Telegram временно ограничил запросы"
    if value in ("Unexpected fetch error",):
        return "Не удалось получить страницу"
    if value.startswith("Telegram preview HTTP "):
        return "Страница Telegram вернула код " + value.rsplit(" ",1)[-1]
    return value
