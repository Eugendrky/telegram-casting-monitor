"""Only user-registered Telegram application credentials are accepted."""

from __future__ import annotations


def resolve_api_credentials(api_id: str, api_hash: str) -> tuple[int, str, bool]:
    api_id, api_hash = (api_id or "").strip(), (api_hash or "").strip()
    if not api_id or not api_hash:
        raise ValueError(
            "Задайте собственные TELEGRAM_API_ID и TELEGRAM_API_HASH через my.telegram.org. "
            "Общие ключи Telegram Desktop больше не используются."
        )
    try:
        identifier = int(api_id)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID должен быть числом") from exc
    if identifier <= 0:
        raise ValueError("TELEGRAM_API_ID должен быть положительным числом")
    return identifier, api_hash, False
