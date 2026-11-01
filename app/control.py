"""State and display text for Telegram bot controls (no Telegram dependencies)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import Settings


@dataclass
class ScanState:
    running: bool = False
    cycles: int = 0
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_sent: int = 0
    total_sent: int = 0
    last_error: str | None = None
    paused: bool = False


def _when(value: datetime | None) -> str:
    if value is None:
        return "ещё не запускался"
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def status_text(state: ScanState, settings: Settings) -> str:
    activity = ("приостановлен из-за ошибки Telegram" if state.paused else
                ("выключен" if not settings.scan_enabled else
                 ("идёт поиск" if state.running else "ожидает следующий запуск")))
    return (
        "📊 Состояние поиска\n\n"
        f"Сейчас: {activity}\n"
        f"Проверок: {state.cycles}\n"
        f"Последний запуск: {_when(state.last_started)}\n"
        f"Последнее завершение: {_when(state.last_finished)}\n"
        f"Найдено за последнюю проверку: {state.last_sent}\n"
        f"Отправлено за время работы: {state.total_sent}\n"
        f"Интервал: {settings.scan_interval_minutes} мин.\n"
        + (f"Последняя ошибка: {state.last_error}" if state.last_error else "")
    ).strip()


def settings_text(
    settings: Settings, source_mode: str = "configured", selected_count: int = 0,
) -> str:
    profile = settings.profile
    mode = f"только выбранные ({selected_count})"
    return (
        "⚙️ Текущие настройки\n\n"
        f"Источники: {mode}\n"
        f"Сбор: {'включён' if settings.scan_enabled else 'отключён (SCAN_ENABLED=false)'}\n"
        f"Город: {profile.city or 'любой'}\n"
        f"Пол: {dict(male='мужской', female='женский', any='любой')[profile.gender]}\n"
        f"Возраст: {profile.age if profile.age is not None else 'любой'}\n"
        f"Рост: {str(profile.height_cm) + ' см' if profile.height_cm is not None else 'любой'}\n"
        f"Детские кастинги: {'да' if profile.kids_ok else 'нет'}\n"
        f"Интервал: {settings.scan_interval_minutes} мин.\n\n"
        "Источники настраиваются через /sources. Профиль (город, пол, возраст) "
        "меняется в файле .env на компьютере."
    )