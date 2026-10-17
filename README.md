# telegram-casting-monitor

Ищет кастинги в **ваших** Telegram-группах и каналах на компьютере и присылает подходящие в чат с ботом (или в Избранное).

Регистрировать приложение на [my.telegram.org](https://my.telegram.org/apps) **не нужно**. BotFather сам по себе группы не читает, поэтому программа один раз входит в Telegram как обычный клиент (телефон + код), а ключи берёт из [открытого исходника Telegram Android](https://github.com/DrKLO/Telegram/blob/master/TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java).

## Как запустить на Mac

1. Установите Python 3.12+, если его ещё нет.
2. В терминале:

```bash
cd ~/telegram-casting-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

3. В `.env` укажите профиль: `PROFILE_CITY`, `PROFILE_GENDER` (`female` / `male` / `any`), `PROFILE_AGE`. Поля `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` оставьте пустыми.

4. (Рекомендуется) Создайте бота у [@BotFather](https://t.me/BotFather) командой `/newbot`, вставьте токен в `BOT_TOKEN`. Потом откройте этого бота и нажмите **Start** — иначе он не сможет вам писать.

5. Первый вход (код придёт в обычный Telegram):

```bash
python scripts/login.py
```

Сессия сохранится в `data/user.session`. Это доступ к аккаунту, файл в git не попадает.

6. Запуск сборщика. Компьютер должен быть включён и в сети:

```bash
python -m app.main
```

Остановка — Ctrl+C. Кастинги придут в чат с ботом; если `BOT_TOKEN` пустой — в Избранное.

В боте: `/start`, `/sources` (какие группы смотреть), `/scan`, `/status`.

Проверка без Telegram:

```bash
python -m unittest discover -s tests
```

## Почему не только бот

Бот из BotFather видит сообщения только в чатах, куда его добавили, и почти не видит историю. Чтобы пройтись по подпискам аккаунта, нужен вход с номером телефона. Он делается **локально**, без облака.

## Railway

Можно оставить на потом. Для сервера после локального логина скопируйте строку сессии из вывода `scripts/login.py` в `TELEGRAM_SESSION` и задайте `PORT` / том `/data`. На компьютере `PORT` не указывайте.
