from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from app.credentials import resolve_api_credentials

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _csv(name: str) -> list[str]:
    raw = _env(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Profile:
    city: str
    gender: str
    age: int | None
    height_cm: int | None
    kids_ok: bool
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    session: str
    profile: Profile
    scan_mode: str
    lookback_hours: int
    messages_per_chat: int
    scan_interval_minutes: int
    max_notify_per_cycle: int
    chat_delay_seconds: float
    sqlite_path: Path
    notify_peer: str
    port: int
    log_level: str
    bot_token: str = ""
    bot_owner_id: int | None = None
    session_path: Path = field(default_factory=lambda: Path("data/user.session"))
    used_official_api: bool = False


def load_settings() -> Settings:
    try:
        api_id, api_hash, used_official_api = resolve_api_credentials(
            _env("TELEGRAM_API_ID"),
            _env("TELEGRAM_API_HASH"),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    session = _env("TELEGRAM_SESSION")
    sqlite_path = Path(_env("SQLITE_PATH", "data/seen.db"))
    session_path = Path(_env("TELEGRAM_SESSION_PATH") or str(sqlite_path.parent / "user.session"))

    age_raw = _env("PROFILE_AGE")
    height_raw = _env("PROFILE_HEIGHT_CM")
    gender = _env("PROFILE_GENDER", "any").lower()
    if gender not in {"male", "female", "any"}:
        raise SystemExit("PROFILE_GENDER должен быть male, female или any")

    scan_mode = _env("SCAN_MODE", "all").lower()
    if scan_mode not in {"all", "title"}:
        raise SystemExit("SCAN_MODE должен быть all или title")

    bot_owner_raw = _env("BOT_OWNER_ID")
    bot_owner_id = int(bot_owner_raw) if bot_owner_raw else None
    if bot_owner_id is not None and bot_owner_id <= 0:
        raise SystemExit("BOT_OWNER_ID должен быть положительным числом")

    port_raw = os.getenv("PORT")
    port = int(port_raw) if port_raw and port_raw.strip() else 0

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        session=session,
        profile=Profile(
            city=_env("PROFILE_CITY"),
            gender=gender,
            age=int(age_raw) if age_raw else None,
            height_cm=int(height_raw) if height_raw else None,
            kids_ok=_env_bool("KIDS_OK", False),
            include_keywords=_csv("INCLUDE_KEYWORDS"),
            exclude_keywords=_csv("EXCLUDE_KEYWORDS")
            or ["набор закрыт", "уже набрали", "итоги кастинга"],
        ),
        scan_mode=scan_mode,
        lookback_hours=_env_int("LOOKBACK_HOURS", 36),
        messages_per_chat=_env_int("MESSAGES_PER_CHAT", 40),
        scan_interval_minutes=_env_int("SCAN_INTERVAL_MINUTES", 20),
        max_notify_per_cycle=_env_int("MAX_NOTIFY_PER_CYCLE", 25),
        chat_delay_seconds=_env_float("CHAT_DELAY_SECONDS", 0.8),
        sqlite_path=sqlite_path,
        notify_peer=_env("NOTIFY_PEER", "me"),
        port=port,
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        bot_token=_env("BOT_TOKEN"),
        bot_owner_id=bot_owner_id,
        session_path=session_path,
        used_official_api=used_official_api,
    )
