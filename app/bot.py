"""Private BotFather bot that controls the existing Telethon user-session scanner."""

from __future__ import annotations

from collections.abc import Callable

from telethon import Button, TelegramClient, events

from app.config import Settings
from app.control import ScanState, settings_text, status_text


class BotController:
    def __init__(
        self,
        client: TelegramClient,
        owner_id: int,
        settings: Settings,
        state: ScanState,
        request_scan: Callable[[], str],
    ) -> None:
        self.client = client
        self.owner_id = owner_id
        self.settings = settings
        self.state = state
        self.request_scan = request_scan

    @staticmethod
    def _menu() -> list[list[Button]]:
        return [
            [Button.inline("🔎 Искать сейчас", b"scan"), Button.inline("📊 Состояние", b"status")],
            [Button.inline("⚙️ Настройки", b"settings"), Button.inline("❔ Помощь", b"help")],
        ]

    def _answer(self, action: str) -> str:
        if action in {"start", "help"}:
            return (
                "🎬 Мой помощник по кастингам\n\n"
                "Ищу подходящие объявления в группах и каналах твоего "
                "Telegram-аккаунта и присылаю находки сюда.\n\n"
                "Нажми «Искать сейчас» для внеочередной проверки "
                "или «Состояние», чтобы увидеть результат."
            )
        if action == "status":
            return status_text(self.state, self.settings)
        if action == "settings":
            return settings_text(self.settings)
        if action == "scan":
            return self.request_scan()
        return "Используй кнопки ниже или команды /start, /status, /scan, /settings."

    def register(self) -> None:
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_message(event: events.NewMessage.Event) -> None:
            # Never reveal scan data or accept commands in a group / from strangers.
            if not event.is_private or event.sender_id != self.owner_id:
                return
            raw = (event.raw_text or "").strip()
            command = raw.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw else ""
            action = {
                "/start": "start",
                "/help": "help",
                "/status": "status",
                "/scan": "scan",
                "/settings": "settings",
            }.get(command, "unknown")
            await event.respond(self._answer(action), buttons=self._menu(), parse_mode=None)

        @self.client.on(events.CallbackQuery())
        async def handle_callback(event: events.CallbackQuery.Event) -> None:
            if event.sender_id != self.owner_id or not event.is_private:
                await event.answer("Недоступно", alert=True)
                return
            action = (event.data or b"").decode("utf-8", errors="replace")
            if action not in {"start", "help", "status", "scan", "settings"}:
                await event.answer("Неизвестное действие", alert=True)
                return
            await event.answer()
            await event.edit(self._answer(action), buttons=self._menu(), parse_mode=None)
