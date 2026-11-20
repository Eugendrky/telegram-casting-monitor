"""First line of a casting card, based on evidence in the announcement."""
import re

from app.casting_quality import casting_text

FORMATS = (
    ("Рекламная съёмка", r"\b(?:реклама\b|рекламн\w*\s+(?:ролик|проект|съемк|кампани)|(?:съемк\w*|роль|кастинг)\s+(?:для |в |на )?реклам|для рекламы|в рекламу)"),
    ("Сериал", r"\b(?:сериал|сериала|сериалов|сериале)\b"),
    ("Короткий метр", r"коротк(?:ий|ого|ом) метр|короткометраж"),
    ("Полный метр", r"полн(?:ый|ого|ом) метр|полнометраж"),
    ("Клип", r"\b(?:видеоклип|клип)(?:а|е|у)?\b"),
    ("Театр", r"\b(?:спектакл|постановк|театр(?:е|а)?\b)"),
    ("Телешоу", r"\b(?:телешоу|ток[- ]шоу|телепередач|телепрограмм)"),
    ("Фильм", r"\b(?:фильм|фильма|фильме)\b"),
)
ROLES = (
    ("Главная роль", r"главн\w*\s+(?:роль|роли|героин|геро[йя])"),
    ("Роль второго плана", r"(?:роль|роли|персонаж)\s+второго плана"),
    ("Эпизод", r"\bэпизод(?:ическ\w*|е|а)?\b|на одну сцену"),
    ("Массовка", r"\bмассовк|массов(?:ых|ые) сцен|на задн[еи]м? план"),
    ("Зрители в студии", r"\bзрител\w*\s+(?:в зал|в студи|на съемк|на ток[- ]шоу)"),
    ("Дублёр", r"\bдублер"),
)


def card_heading(text: str, role: str = "") -> str:
    body = casting_text(text).casefold().replace("ё", "е")
    fragment = role.casefold().replace("ё", "е") if role else body
    project = next((name for name, pattern in FORMATS if re.search(pattern, body)), "Формат не указан")
    fragment = re.sub(r"\b(?:не|без)\s+(?:массовк\w*|эпизод\w*|реплик\w*)\b", "", fragment)
    types = [name for name, pattern in ROLES if re.search(pattern, fragment)]
    if len(types) == 1:
        kind = types[0]
        if kind == "Эпизод" and re.search(r"с (?:текстом|реплик)|\b(?:две|три|одна|\d+) реплик", fragment):
            kind = "Эпизод с репликами"
    elif not types and re.search(r"\b(?:актрис|актер|на роль|роль\s*:)", fragment):
        kind = "Актёрская роль"
    else:
        kind = "Несколько типов ролей" if types else "Тип роли не указан"
    return f"🎬 {project} · {kind}"
