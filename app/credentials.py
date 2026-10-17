"""API identifiers for a local user login without my.telegram.org.

BotFather cannot read groups you merely subscribed to. A user session still
needs api_id/api_hash. When you cannot register an app, we fall back to the
identifiers published in the official Telegram Android source.
"""

from __future__ import annotations

# https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java
OFFICIAL_ANDROID_API_ID = 4
OFFICIAL_ANDROID_API_HASH = "014b35b6184100b085b0d0572f9b5103"


def resolve_api_credentials(api_id: str, api_hash: str) -> tuple[int, str, bool]:
    """Return (api_id, api_hash, used_official_fallback)."""
    api_id = (api_id or "").strip()
    api_hash = (api_hash or "").strip()
    if api_id and api_hash:
        return int(api_id), api_hash, False
    if api_id or api_hash:
        raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH нужно задавать вместе")
    return OFFICIAL_ANDROID_API_ID, OFFICIAL_ANDROID_API_HASH, True
