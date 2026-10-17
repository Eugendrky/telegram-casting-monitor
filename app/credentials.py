"""API identifiers for a local user login without my.telegram.org.

BotFather cannot read groups you merely subscribed to. A user session still
needs api_id/api_hash. When you cannot register an app, we fall back to the
identifiers published in the official Telegram Desktop source.
"""

from __future__ import annotations

# Official Telegram Desktop API identifiers (widely used and currently not flooded).
# Source: https://github.com/telegramdesktop/tdesktop (and opentele)
OFFICIAL_DESKTOP_API_ID = 2040
OFFICIAL_DESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"


def resolve_api_credentials(api_id: str, api_hash: str) -> tuple[int, str, bool]:
    """Return (api_id, api_hash, used_official_fallback)."""
    api_id = (api_id or "").strip()
    api_hash = (api_hash or "").strip()
    if api_id and api_hash:
        return int(api_id), api_hash, False
    if api_id or api_hash:
        raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH нужно задавать вместе")
    return OFFICIAL_DESKTOP_API_ID, OFFICIAL_DESKTOP_API_HASH, True
