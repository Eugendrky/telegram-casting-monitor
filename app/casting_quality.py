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
ROLE = re.compile(r"(акт[её]р|актрис|модел|массовк|типаж|парень|парн|девушк|"
                  r"мужчин|женщин|персонаж|роль|роли|геро|участник|детей)", re.I)
CASTING = re.compile(r"(кастинг|кинопроб|пробы|съ[её]мк|съ[её]мок|в кино|"
                     r"в сериал|в рекламу|в клип|casting|extras)", re.I)
STRUCTURED_ROLE_CASTING = (
    re.compile(r"\b(?:роль|роли)\s*:", re.I),
    re.compile(r"\b(?:дата|даты)\s*:", re.I),
    re.compile(r"\b(?:ставка|гонорар|оплата)\s*:", re.I),
    re.compile(r"(?:заявк[аиу]|для отклика|присылайте|отправьте)", re.I),
)
DEADLINE = re.compile(
    r"(?:дедлайн|заявки до|заявления до|отклики до|при[её]м заявок до|"
    r"подача до|отправить до)\s*[:\-]?\s*(\d{1,2})[./](\d{1,2})[./](20\d{2})",
    re.I,
)


URL = re.compile(r"(?:https?://|www[.])\S+", re.I)
STREAM_HEADER = re.compile(r"^[^\w]*(?:twitch|твич|[сc]тримы)\s*(?:[|:]|$)", re.I)
DONATION = re.compile(r"donationalerts[.]com/|на ход ноги|на донаты", re.I)


def casting_text(text: str) -> str:
    """Remove a streamer's social/donation block before classification and display."""
    text = text or ""
    if not DONATION.search(text):
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not STREAM_HEADER.search(line.strip()):
            continue
        end = index + 1
        while end < len(lines):
            plain = re.sub(r"#\w+", "", URL.sub("", lines[end]))
            if CASTING.search(plain) or (RECRUITMENT.search(plain) and ROLE.search(plain)):
                break
            end += 1
        while index and not re.search(r"\w", lines[index - 1]):
            index -= 1
        return "\n".join(lines[:index] + lines[end:]).strip()
    return text


def actionable(text: str, today: date | None = None) -> tuple[bool, str]:
    normalized = " ".join(URL.sub("", casting_text(text)).lower().replace("ё", "е").split())
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
    # Some established casting channels use a compact form with structured
    # fields instead of repeating the word "кастинг" in every post. Accept
    # only the full role + date + pay + application pattern to avoid turning
    # generic job notices into casting candidates.
    structured_role = all(pattern.search(normalized)
                          for pattern in STRUCTURED_ROLE_CASTING)
    if not CASTING.search(normalized) and not structured_role:
        return False, "нет признаков съёмки/кастинга"
    if not (RECRUITMENT.search(normalized) or ROLE.search(normalized)):
        return False, "нет признаков набора на роль"
    return True, "открытый набор вероятен"
