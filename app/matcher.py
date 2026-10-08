from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Profile

_YO = str.maketrans({"ё": "е", "Ё": "е"})

CASTING_HINTS = (
    "кастинг",
    "кинопроб",
    "пробы ",
    " на пробы",
    "съемк",
    "съемка",
    "съемки",
    "массовк",
    "типаж",
    "типажи",
    "ищем актер",
    "ищем актрис",
    "нужны актер",
    "нужны актрис",
    "требуются актер",
    "требуются актрис",
    "актеров и актрис",
    "актёров",
    "tfp",
    "t.f.p",
    "рекламный ролик",
    "в рекламу",
    "кинопроект",
    "в сериал",
    "в клип",
    "моделей",
    "модельный кастинг",
    "extras",
    "casting",
    "looks like",
    "lookalike",
)

TITLE_HINTS = (
    "кастинг",
    "съемк",
    "съемка",
    "актер",
    "актёр",
    "актрис",
    "модел",
    "кино",
    "реклам",
    "массовк",
    "типаж",
    "пробы",
    "casting",
    "film",
    "actor",
)

KIDS_HINTS = (
    "дети",
    "детей",
    "ребенок",
    "ребенка",
    "ребенку",
    "малыш",
    "школьник",
    "школьниц",
    "подростк",
    "детский кастинг",
    "детская массовка",
)
KIDS_AGE_RE = re.compile(r"\b(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*(?:лет|года)")

FEMALE_HINTS = (
    "девушк",
    "девочек",
    "женщин",
    "женск",
    "актрис",
    "female",
    "women",
    "woman",
    "girls",
)

MALE_HINTS = (
    "парней",
    "парня",
    "парни",
    "мужчин",
    "мужск",
    "актеров",
    "male",
    "men",
    "man",
    "boys",
)

BOTH_HINTS = (
    "парней и девушек",
    "девушек и парней",
    "актеров и актрис",
    "актрис и актеров",
    "мужчин и женщин",
    "женщин и мужчин",
    "м/ж",
    "ж/м",
)

CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "москва": ("москв", "мск", "moscow"),
    "санкт-петербург": ("санкт-петербург", "петербург", "питер", "спб", "spb"),
    "екатеринбург": ("екатеринбург", "екб"),
    "новосибирск": ("новосибирск", "нск"),
    "казань": ("казан",),
    "нижний новгород": ("нижний новгород", "нижнем новгород"),
    "краснодар": ("краснодар",),
    "ростов": ("ростов-на-дону", "ростов"),
    "самара": ("самар",),
    "воронеж": ("воронеж",),
    "красноярск": ("красноярск",),
    "пермь": ("пермь", "перми"),
    "уфа": ("уфа", "уфе"),
    "тюмень": ("тюмен",),
    "сочи": ("сочи",),
    "калининград": ("калининград",),
    "минск": ("минск",),
    "киев": ("киев", "київ", "kyiv"),
}

AGE_RANGE_RE = re.compile(
    r"(?:возраст|лет|года?)?[^\d]{0,8}(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*(?:лет|г\b|года)?"
)
AGE_FROM_RE = re.compile(r"(?:от|с)\s*(\d{1,2})\s*(?:до\s*(\d{1,2}))?\s*(?:лет|года)?")
AGE_TO_RE = re.compile(r"(?:до|не старше)\s*(\d{1,2})\s*(?:лет|года)?")
HEIGHT_RANGE_RE = re.compile(r"(?:рост)?[^\d]{0,6}(\d{2,3})\s*[-–—]\s*(\d{2,3})\s*(?:см)?")
HEIGHT_FROM_RE = re.compile(r"рост[^\d]{0,10}(?:от\s*)?(\d{2,3})")


@dataclass
class MatchResult:
    matched: bool
    score: int
    reasons: list[str]


def norm(text: str) -> str:
    return (text or "").translate(_YO).lower()


def is_casting_title(title: str) -> bool:
    n = norm(title)
    return any(hint in n for hint in TITLE_HINTS)


def _contains_any(text: str, hints: tuple[str, ...] | list[str]) -> bool:
    return any(hint in text for hint in hints)


def _city_keys(city: str) -> list[str]:
    n = norm(city)
    if not n:
        return []
    for key, aliases in CITY_ALIASES.items():
        if n == key or n in aliases:
            return [key]
    return [n]


def _mentioned_cities(text: str) -> set[str]:
    found: set[str] = set()
    for key, aliases in CITY_ALIASES.items():
        if any(alias in text for alias in aliases):
            found.add(key)
    return found


def _age_windows(text: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for match in AGE_RANGE_RE.finditer(text):
        lo, hi = int(match.group(1)), int(match.group(2))
        if 4 <= lo <= hi <= 90:
            windows.append((lo, hi))
    for match in AGE_FROM_RE.finditer(text):
        lo = int(match.group(1))
        hi = int(match.group(2) or 90)
        if 4 <= lo <= 90:
            windows.append((lo, hi))
    for match in AGE_TO_RE.finditer(text):
        hi = int(match.group(1))
        if 4 <= hi <= 90:
            windows.append((4, hi))
    return windows


def _height_windows(text: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for match in HEIGHT_RANGE_RE.finditer(text):
        lo, hi = int(match.group(1)), int(match.group(2))
        if 120 <= lo <= hi <= 220:
            windows.append((lo, hi))
    if "рост" in text:
        for match in HEIGHT_FROM_RE.finditer(text):
            value = int(match.group(1))
            if 120 <= value <= 220:
                windows.append((value - 3, value + 8))
    return windows


def match_post(text: str, profile: Profile) -> MatchResult:
    body = norm(text)
    reasons: list[str] = []
    score = 0

    if not body.strip():
        return MatchResult(False, 0, ["нет текста"])

    if _contains_any(body, profile.exclude_keywords):
        return MatchResult(False, 0, ["стоп-слово"])

    if not profile.kids_ok:
        if _contains_any(body, KIDS_HINTS):
            return MatchResult(False, 0, ["детский кастинг"])
        for match in KIDS_AGE_RE.finditer(body):
            lo, hi = int(match.group(1)), int(match.group(2))
            if hi <= 16 and lo <= 14:
                return MatchResult(False, 0, ["детский кастинг"])

    hint_hits = [hint for hint in CASTING_HINTS if hint in body]
    extra_hits = [kw for kw in profile.include_keywords if norm(kw) in body]
    if not hint_hits and not extra_hits:
        return MatchResult(False, 0, ["не кастинг"])

    score += min(5, len(hint_hits) + len(extra_hits))
    reasons.append("кастинг: " + ", ".join((hint_hits + extra_hits)[:4]))

    both = _contains_any(body, BOTH_HINTS)
    female = _contains_any(body, FEMALE_HINTS)
    male = _contains_any(body, MALE_HINTS)
    if profile.gender == "female" and male and not female and not both:
        return MatchResult(False, score, reasons + ["только мужской типаж"])
    if profile.gender == "male" and female and not male and not both:
        return MatchResult(False, score, reasons + ["только женский типаж"])
    if profile.gender == "female" and (female or both):
        score += 2
        reasons.append("женский/смешанный типаж")
    if profile.gender == "male" and (male or both):
        score += 2
        reasons.append("мужской/смешанный типаж")

    if profile.age is not None:
        windows = _age_windows(body)
        if windows and not any(lo <= profile.age <= hi for lo, hi in windows):
            return MatchResult(False, score, reasons + ["возраст не подходит"])
        if windows:
            score += 2
            reasons.append("возраст в диапазоне")

    if profile.height_cm is not None:
        windows = _height_windows(body)
        if windows and not any(lo <= profile.height_cm <= hi for lo, hi in windows):
            return MatchResult(False, score, reasons + ["рост не подходит"])
        if windows:
            score += 1
            reasons.append("рост в диапазоне")

    if profile.city:
        keys = _city_keys(profile.city)
        mentioned = _mentioned_cities(body)
        profile_mentioned = any(
            key in mentioned or any(alias in body for alias in CITY_ALIASES.get(key, (key,)))
            for key in keys
        )
        other = mentioned - set(keys)
        online = any(word in body for word in ("онлайн", "удаленн", "самоявок", "self-tape", "селфтейп"))
        if other and not profile_mentioned and not online:
            return MatchResult(False, score, reasons + [f"другой город: {', '.join(sorted(other))}"])
        if profile_mentioned:
            score += 3
            reasons.append(f"город {profile.city}")
        elif online:
            score += 1
            reasons.append("онлайн/самоявка")

    return MatchResult(True, score, reasons)
