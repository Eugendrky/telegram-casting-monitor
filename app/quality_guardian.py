"""Bounded read-only quality monitoring. Never reads Telegram user sessions.

This is a deterministic server process, not an autonomous coding agent.
It reports availability, delivery backlog and basic casting-filter regressions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from app.casting_quality import actionable
from app.config import Profile
from app.matcher import match_post

LOG = logging.getLogger("casting.guardian")
SNAPSHOT_KEY = "guardian:last_snapshot"
ALERT_KEY = "guardian:last_alert"
RECOMMENDED_SOURCE_AGE = 7200
ALERT_COOLDOWN = 86400


def filter_fixtures() -> tuple[int, int]:
    profile = Profile(city="Москва", gender="female", age=32, height_cm=165,
                      kids_ok=False, include_keywords=[], exclude_keywords=[])
    cases = [
        ("Кастинг Москва. Ищем актрису 30-35 лет для съемок рекламы.", True),
        ("Кастинг Москва. Нужны мужчины 20-30 лет для съемок.", False),
        ("Кастинг в Москве. Набор закрыт. Ищем актрису 30-35 лет.", False),
        ("Кастинг в Казани. Ищем актрису 30-35 лет для съемок.", False),
        ("Продажа оборудования и курсы актерского мастерства.", False),
    ]
    passed = sum(match_post(text, profile).matched == expected for text, expected in cases)
    return passed, len(cases)


def inspect(db, *, now: int | None = None) -> dict:
    """Read aggregated counters only. No message text or user identities."""
    stamp = int(time.time()) if now is None else int(now)
    sources = db.public_sources()
    enabled = {row[1] for row in sources if row[3] and row[4]}
    states = {str(row[0]): row for row in db.public_status()}
    failed, fresh, never, stale = [], [], [], []
    for title in sorted(enabled):
        row = states.get(str(title))
        if row is None or not row[4]:
            never.append(title)
        elif row[2]:
            failed.append(title)
        elif row[5] and stamp - row[5] <= RECOMMENDED_SOURCE_AGE:
            fresh.append(title)
        else:
            stale.append(title)
    delivery = db.delivery_stats()
    counts = db.admin_counts()
    passed, total = filter_fixtures()
    findings = []
    if failed:
        findings.append("Недоступные источники: " + ", ".join(failed[:8]))
    if never:
        findings.append("Не проходили проверку: " + ", ".join(never[:8]))
    if stale:
        findings.append("Давно нет подтверждённого чтения: " + ", ".join(stale[:8]))
    if delivery.get("failed", 0):
        findings.append(f"Ошибка доставки уведомлений: {delivery['failed']}")
    if delivery.get("pending", 0) >= 20:
        findings.append(f"Накопилась очередь уведомлений: {delivery['pending']}")
    if passed != total:
        findings.append(f"Регрессия фильтрации: {passed}/{total} контрольных примеров")
    if not enabled:
        findings.append("Не включён ни один разрешённый источник")
    severity = "problem" if findings else "ok"
    return {
        "checked_at": stamp,
        "severity": severity,
        "sources": {"enabled": len(enabled), "fresh": len(fresh),
                    "unavailable": len(failed), "unchecked": len(never),
                    "stale": len(stale)},
        "delivery": {key: int(delivery.get(key, 0)) for key in ("sent", "pending", "failed", "blocked")},
        "posts_archived": int(counts["posts"]),
        "filter_checks": {"passed": passed, "total": total},
        "findings": findings,
        "login_status": "QR-сессии отсутствуют в действующей версии",
    }


def render(snapshot: dict) -> str:
    sources = snapshot["sources"]
    sent = snapshot["delivery"]
    checks = snapshot["filter_checks"]
    text = (
        "🛡 Проверка качества бота\n"
        f"Источники: {sources['enabled']} включены; "
        f"{sources['fresh']} успешно; {sources['unavailable']} недоступны; "
        f"{sources['unchecked']} не проверены; {sources['stale']} устарели.\n"
        f"Кастингов в базе: {snapshot['posts_archived']}.\n"
        f"Уведомления: {sent['sent']} доставлено, {sent['pending']} ожидают, "
        f"{sent['failed']} ошибок, {sent['blocked']} блокировок.\n"
        f"Контроль фильтра: {checks['passed']}/{checks['total']} примеров.\n"
        "QR-вход и чтение групп: в этой версии не запущены."
    )
    if snapshot["findings"]:
        text += "\n\nПроблемы:\n" + "\n".join("• " + item for item in snapshot["findings"][:9])
    return text[:3700]


def save_snapshot(db, snapshot: dict):
    """One key in existing SQLite meta; survives Railway restarts."""
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    with db._lock, db.connection:
        db.connection.execute(
            """INSERT INTO meta(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (SNAPSHOT_KEY, data),
        )


def read_snapshot(db) -> dict | None:
    with db._lock:
        row = db.connection.execute("SELECT value FROM meta WHERE key=?", (SNAPSHOT_KEY,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def claim_alert(db, now: int, *, cooldown: int = ALERT_COOLDOWN) -> bool:
    """Persist alert claim first to avoid a flood after process restarts."""
    with db._lock, db.connection:
        row = db.connection.execute("SELECT value FROM meta WHERE key=?", (ALERT_KEY,)).fetchone()
        if row is not None:
            try:
                if now - int(row[0]) < cooldown:
                    return False
            except (ValueError, TypeError):
                pass
        db.connection.execute(
            """INSERT INTO meta(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (ALERT_KEY, str(now)),
        )
    return True


async def check_once(db, api=None, owner_id: int = 0, *,
                     alerts: bool = False, now: int | None = None) -> dict:
    report = inspect(db, now=now)
    save_snapshot(db, report)
    LOG.info("Quality check: sources=%s healthy=%s problems=%s filters=%s/%s",
             report["sources"]["enabled"], report["sources"]["fresh"],
             len(report["findings"]), report["filter_checks"]["passed"],
             report["filter_checks"]["total"])
    if (alerts and owner_id > 0 and api is not None and report["severity"] == "problem"
            and claim_alert(db, report["checked_at"])):
        try:
            await asyncio.to_thread(api.send, owner_id, render(report))
        except Exception as exc:
            LOG.warning("Failed to send quality alert: %s", type(exc).__name__)
    return report


async def run_guardian(db, api, owner_id: int, *, interval: int = 3600,
                       alerts: bool = False):
    """Background scan of our *own state*; no Telegram group or web requests."""
    while True:
        try:
            await check_once(db, api, owner_id, alerts=alerts)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("Quality check failed")
        await asyncio.sleep(max(900, int(interval)))
