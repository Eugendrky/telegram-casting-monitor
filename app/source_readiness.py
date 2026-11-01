"""Truthful source readiness diagnostics. No authentication or new network traffic."""
from __future__ import annotations

from app.locale_ru import readable_time, source_error

REQUIRED = ("castingiot4000r", "kinocekh", "moscowfilms")


def public_source_summary(db):
    """Classify only observed scans, never invent availability."""
    rows=db.public_status()
    issues=0
    successes=0
    lines=[]
    for title,cursor,error,next_scan,checked,successful,posts,new,matched in rows:
        if error:
            issues+=1
            label="⛔ Нет доступа: "+source_error(error)
        elif not checked:
            label="⏳ Ещё не проверен"
        elif successful and successful>=checked:
            successes+=1
            label=f"✅ Проверен: публикаций {posts}, новых {new}, сохранено {matched}"
        else:
            label="⚠️ Проверка не подтверждена"
        lines.append(f"{title}: {label}\nПоследняя проверка: {readable_time(checked)}")
    return {"total":len(rows),"unavailable":issues,"confirmed":successes,"lines":lines,
            "none_available":bool(rows) and successes==0}


def readiness_text(db, *, login_ready: bool=False) -> str:
    info=public_source_summary(db)
    lines=[
        "🧪 Готовность поиска кастингов",
        f"Источников: {info['total']}. Недоступны: {info['unavailable']}. "
        f"Успешных последних проверок: {info['confirmed']}.",
        "Авторизация Telegram Web: " + ("требует проверки" if login_ready else "не подключена"),
        "Чтение трёх групп после QR: не подтверждено.",
    ]
    lines.extend(info["lines"])
    if info["none_available"]:
        lines.append("⚠️ Нет подтверждённого доступа к источникам. "
                     "Отсутствие результатов не означает отсутствие кастингов.")
    return "\n\n".join(lines)[:3800]


def empty_castings_text(db) -> str:
    health=public_source_summary(db)
    if health["none_available"]:
        return ("Кастинги пока недоступны: нет подтверждённого доступа "
                "к источникам. Это не означает, что объявлений нет. "
                "Проверить причины: /ready")
    return "Подходящих кастингов пока нет. Состояние источников: /ready"
