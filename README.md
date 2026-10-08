# telegram-casting-monitor

Воркер для Railway: ходит по **вашим** Telegram-группам и каналам (подписки аккаунта), отбирает посты про кастинги под ваш профиль и присылает их в **Избранное**.

Это не BotFather-бот. Нужен обычный аккаунт + `api_id` / `api_hash` с [my.telegram.org/apps](https://my.telegram.org/apps).

## Что делает

1. Раз в `SCAN_INTERVAL_MINUTES` читает недавние сообщения из диалогов-групп/каналов.
2. Оставляет посты со словами вроде «кастинг», «съёмка», «типаж», «массовка».
3. Отсекает неподходящие по городу, полу, возрасту, росту, детским наборам и стоп-словам.
4. Уже просмотренные id хранит в SQLite, чтобы не слать одно и то же.

## Локальный логин (обязательно до Railway)

```bash
cd telegram-casting-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните TELEGRAM_API_ID и TELEGRAM_API_HASH
python scripts/login.py
```

Код придёт в Telegram. Строку `TELEGRAM_SESSION` скопируйте в `.env` и в переменные Railway. Сессия = доступ к аккаунту, не публикуйте её.

Профиль поменяйте в `.env`: `PROFILE_CITY`, `PROFILE_GENDER` (`female` / `male` / `any`), `PROFILE_AGE`, при необходимости `PROFILE_HEIGHT_CM`.

Проверка матчера:

```bash
python -m unittest discover -s tests
```

Локальный прогон (уже с сессией в `.env`):

```bash
python -m app.main
```

Остановите Ctrl+C. На время отладки удобно `LOOKBACK_HOURS=6` и `SCAN_MODE=title` (только чаты с «кастинг/кино/актёр» в названии).

## Railway

1. Новый проект → задеплойте этот репозиторий (Dockerfile уже есть).
2. Variables: все ключи из `.env.example`, **кроме** пустых, плюс готовый `TELEGRAM_SESSION`.
3. Volume, смонтированный в `/data`, чтобы `SQLITE_PATH=/data/seen.db` переживал рестарты.
4. Сервис слушает `PORT` (health `ok`) и крутит сканер в том же процессе.

Первый цикл может занять несколько минут: между чатами пауза `CHAT_DELAY_SECONDS`, чтобы меньше ловить FloodWait.

## Важно

- Собирает только то, что уже видно этому аккаунту. Приватные чаты без участия не читаются.
- Слишком частый обход → FloodWait или ограничения аккаунта. Не ставьте интервал в 1 минуту на сотни групп.
- Лучше отдельный Telegram-аккаунт, не основной.
