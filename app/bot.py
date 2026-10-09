"""Private Telegram interface for the Telethon user-session casting monitor."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from telethon import Button, TelegramClient, events
from telethon.errors import RPCError

from app.config import Settings
from app.control import ScanState, settings_text, status_text
from app.sources import is_source_dialog
from app.store import Store

log = logging.getLogger("castings.bot")
PAGE_SIZE = 8
CACHE_SECONDS = 120


class BotController:
    def __init__(
        self,
        client: TelegramClient,
        owner_id: int,
        settings: Settings,
        state: ScanState,
        request_scan: Callable[[], str],
        user_client: TelegramClient,
        store: Store,
    ) -> None:
        self.client = client
        self.owner_id = owner_id
        self.settings = settings
        self.state = state
        self.request_scan = request_scan
        self.user_client = user_client
        self.store = store
        self._sources_cache: list[tuple[int, str, str]] | None = None
        self._sources_loaded_at = 0.0

    @staticmethod
    def _menu() -> list[list[Button]]:
        return [
            [Button.inline("🔎 Искать сейчас", b"scan"), Button.inline("📊 Состояние", b"status")],
            [Button.inline("📚 Источники", b"src:page:0"), Button.inline("⚙️ Настройки", b"settings")],
            [Button.inline("❔ Помощь", b"help")],
        ]

    def _answer(self, action: str) -> str:
        if action in {"start", "help"}:
            return (
                "🎬 Мой помощник по кастингам\n\n"
                "Собираю подходящие объявления из Telegram-групп и каналов, "
                "доступных твоему аккаунту, и присылаю сюда.\n\n"
                "Открой «Источники», чтобы выбрать группы и каналы. "
                "Используй «Искать сейчас» для внеочередной проверки."
            )
        if action == "status":
            return status_text(self.state, self.settings)
        if action == "settings":
            return settings_text(
                self.settings,
                source_mode=self.store.source_mode(),
                selected_count=len(self.store.selected_source_ids()),
            )
        if action == "scan":
            return self.request_scan()
        return "Используй кнопки ниже или команды /start, /sources, /status, /scan, /settings."

    async def _available_sources(self, refresh: bool = False) -> list[tuple[int, str, str]]:
        if (not refresh and self._sources_cache is not None
                and time.monotonic() - self._sources_loaded_at < CACHE_SECONDS):
            return self._sources_cache
        sources = []
        async for dialog in self.user_client.iter_dialogs():
            if is_source_dialog(dialog):
                kind = "👥" if dialog.is_group else "📣"
                sources.append((dialog.id, dialog.name or "Без названия", kind))
        sources.sort(key=lambda item: (item[1].casefold(), item[0]))
        self._sources_cache = sources
        self._sources_loaded_at = time.monotonic()
        return sources

    async def _sources_screen(
        self, page: int, refresh: bool = False,
    ) -> tuple[str, list[list[Button]]]:
        sources = await self._available_sources(refresh=refresh)
        selected = self.store.selected_source_ids()
        mode = self.store.source_mode()
        page_count = max(1, (len(sources) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, page_count - 1))
        if mode == "selected":
            state = f"только выбранные ({len(selected)})"
        elif mode == "all":
            state = "все группы и каналы"
        else:
            state = f"по настройке Railway ({self.settings.scan_mode})"
        text = (
            f"📚 Источники кастингов\n\nРежим: {state}\n"
            f"Доступно групп и каналов: {len(sources)}\n"
            f"Страница {page + 1}/{page_count}\n\n"
            "Нажми на название, чтобы включить или исключить источник. "
            "После первого выбора поиск переключится на выбранные группы. "
            "Изменения вступают в силу со следующей проверки."
        )
        if mode == "selected" and not selected:
            text += "\n\n⚠️ Не выбрано ни одного источника: поиск приостановлен."
        if not sources:
            text += "\n\nНа подключённом Telegram-аккаунте нет доступных групп или каналов."

        buttons: list[list[Button]] = []
        for chat_id, title, kind in sources[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            mark = "✅" if chat_id in selected else "▫️"
            buttons.append([Button.inline(f"{mark} {kind} {title[:42]}", f"src:toggle:{chat_id}:{page}".encode())])
        navigation: list[Button] = []
        if page > 0:
            navigation.append(Button.inline("⬅️ Назад", f"src:page:{page - 1}".encode()))
        if page + 1 < page_count:
            navigation.append(Button.inline("Далее ➡️", f"src:page:{page + 1}".encode()))
        if navigation:
            buttons.append(navigation)
        buttons.append([Button.inline("🌍 Все источники", b"src:mode:all"),
                        Button.inline("☑️ Только выбранные", b"src:mode:selected")])
        buttons.append([Button.inline("🔄 Обновить", f"src:refresh:{page}".encode()),
                        Button.inline("🏠 Меню", b"start")])
        return text, buttons

    async def _source_callback(self, event: events.CallbackQuery.Event, action: str) -> None:
        parts = action.split(":")
        if len(parts) < 3 or parts[0] != "src":
            await event.answer("Неизвестная команда", alert=True)
            return
        try:
            kind = parts[1]
            page = 0
            if kind in {"page", "refresh"} and len(parts) == 3:
                page = int(parts[2])
                if page < 0:
                    raise ValueError("negative page")
            elif kind == "toggle" and len(parts) == 4:
                chat_id, page = int(parts[2]), int(parts[3])
                if page < 0:
                    raise ValueError("negative page")
            elif kind == "mode" and len(parts) == 3 and parts[2] in {"all", "selected"}:
                pass
            else:
                raise ValueError("invalid callback")
        except ValueError:
            await event.answer("Некорректная команда", alert=True)
            return

        try:
            if kind == "toggle":
                sources = await self._available_sources()
                candidate = next((s for s in sources if s[0] == chat_id), None)
                if candidate is None:
                    await event.answer("Источник недоступен. Обнови список.", alert=True)
                    return
                self.store.toggle_source(candidate[0], candidate[1])
            elif kind == "mode":
                self.store.set_source_mode(parts[2])
            text, buttons = await self._sources_screen(page, refresh=(kind == "refresh"))
            await event.answer()
            await event.edit(text, buttons=buttons, parse_mode=None)
        except RPCError:
            log.exception("Не удалось обновить Telegram-источники")
            await event.answer("Не удалось загрузить источники. Повтори позже.", alert=True)

    def register(self) -> None:
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_message(event: events.NewMessage.Event) -> None:
            if not event.is_private or event.sender_id != self.owner_id:
                return
            raw = (event.raw_text or "").strip()
            command = raw.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw else ""
            if command == "/sources":
                try:
                    text, buttons = await self._sources_screen(0)
                except RPCError:
                    log.exception("Не удалось загрузить Telegram-источники")
                    await event.respond("Не удалось загрузить источники. Повтори позже.", parse_mode=None)
                    return
                await event.respond(text, buttons=buttons, parse_mode=None)
                return
            action = {
                "/start": "start", "/help": "help", "/status": "status",
                "/scan": "scan", "/settings": "settings",
            }.get(command, "unknown")
            await event.respond(self._answer(action), buttons=self._menu(), parse_mode=None)

        @self.client.on(events.CallbackQuery())
        async def handle_callback(event: events.CallbackQuery.Event) -> None:
            if event.sender_id != self.owner_id or not event.is_private:
                await event.answer("Недоступно", alert=True)
                return
            action = (event.data or b"").decode("utf-8", errors="replace")
            if action.startswith("src:"):
                await self._source_callback(event, action)
                return
            if action not in {"start", "help", "status", "scan", "settings"}:
                await event.answer("Неизвестное действие", alert=True)
                return
            await event.answer()
            await event.edit(self._answer(action), buttons=self._menu(), parse_mode=None)