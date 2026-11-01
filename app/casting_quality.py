"""Conservative casting quality checks applied before matching user attributes.

No network calls, invented requirements, or learning from unverified sources.
False negatives/positives are tracked with explicit user feedback.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

CLOSED = (
    "набор закрыт", "кастинг закрыт", "кастинг завершен",
    "кастинг завершён", "прием заявок завершен", "приём заявок завершён",
    "прием заявок закрыт", "приём заявок закрыт",
    "заявки больше не принимаются", "все роли утверждены",
)
ADVERTISING = (
    "курс актерского", "курс актёрского", "курсы актерского",
    "курсы актёрского", "уроки актерского", "обучение актерскому",
    "обучение актёрскому", "кастинг-директор обучение",
    "курс по кастингам", "как пройти кастинг",
    "мастер-класс по кастингам", "запишитесь на курс",
)
RETROSPECTIVE = (
    "итоги кастинга", "кастинг состоялся", "кастинг прошел",
    "кастинг прошёл", "как проходил кастинг",
)
LOOKING_FOR_WORK = ("ищу кастинг", "ищу работу актер", "ищу работу актрис", "готов сниматься")
RECRUITMENT = re.compile(
    r"\b(ищем|нужен|нужна|нужны|требуется|требуются|приглашаем|"
    r"набираем|отбираем|разыскиваем|продолжается набор)\b",
    re.I,
)
ROLE = re.compile(r"(акт[её]р|актрис|модел|массовк|типаж|парн|девушк|"
                  r"мужчин|женщин|персонаж|роль|роли|геро|участник|детей)", re.I)
CASTING = re.compile(r"(кастинг|кинопроб|пробы|съ[её]мк|съ[её]мок|в кино|"
                     r"в сериал|в рекламу|в клип|casting|extras)", re.I)
DEADLINE = re.compile(
    r"(?:дедлайн|заявки до|заявления до|отклики до|при[её]м заявок до|"
    r"подача до|отправить до)\s*[:\-]?\s*(\d{1,2})[./](\d{1,2})[./](20\d{2})",
    re.I,
)


def actionable(text: str, today: date | None = None) -> tuple[bool, str]:
    normalized = " ".join((text or "").lower().replace("ё", "е").split())
    if not normalized:
        return False, "пустая публикация"
    if any(term.replace("ё", "е") in normalized for term in CLOSED):
        return False, "набор закрыт"
    if any(term.replace("ё", "е") in normalized for term in ADVERTISING):
        return False, "объявление об обучении"
    if any(term.replace("ё", "е") in normalized for term in RETROSPECTIVE):
        return False, "прошедший кастинг"
    if any(term in normalized for term in LOOKING_FOR_WORK):
        return False, "резюме соискателя"
    today = today or datetime.now(timezone.utc).date()
    for match in DEADLINE.finditer(normalized):
        try:
            deadline = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            continue
        if deadline < today:
            return False, "срок подачи истёк"
    if not CASTING.search(normalized):
        return False, "нет признаков съёмки/кастинга"
    if not (RECRUITMENT.search(normalized) or ROLE.search(normalized)):
        return False, "нет признаков набора на роль"
    return True, "открытый набор вероятен"
