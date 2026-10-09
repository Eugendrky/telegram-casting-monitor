from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

from app.bot import BotController
from app.config import Settings, load_settings
from app.control import ScanState
from app.links import message_link
from app.matcher import match_post
from app.store import Store
from app.sources import source_allowed

log = logging.getLogger("castings")
Notify = Callable[[str], Awaitable[None]]


def _format_notice(dialog: Dialog, message: Message, reasons: list[str], score: int) -> str:
    text = (message.raw_text or "").strip()
    if len(text) > 1800:
        text = text[:1800] + "…"
    when = message.date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🎬 Кастинг · {score}\n"
        f"Чат: {dialog.name}\n"
        f"Когда: {when}\n"
        f"Почему: {'; '.join(reasons)}\n\n"
        f"{text}\n\n"
        f"{message_link(message)}"
    )


async def _health_server(port: int) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            body = b"ok"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    log.info("health-check на порту %s", port)
    return server


async def scan_once(
    client: TelegramClient,
    settings: Settings,
    store: Store,
    notify: Notify | None = None,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.lookback_hours)
    sent = 0
    dialogs = 0

    if notify is None:
        async def notify(text: str) -> None:
            await client.send_message(settings.notify_peer, text, parse_mode=None, link_preview=False)

    # Take a consistent snapshot: bot changes take effect in the next cycle.
    source_mode = store.source_mode()
    chosen_sources = store.selected_source_ids() if source_mode == "selected" else set()

    async for dialog in client.iter_dialogs():
        if not source_allowed(dialog, settings.scan_mode, source_mode, chosen_sources):
            continue
        dialogs += 1
        try:
            async for message in client.iter_messages(dialog.entity, limit=settings.messages_per_chat):
                if message.date is None:
                    continue
                msg_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
                if msg_date < cutoff:
                    break
                if store.is_seen(dialog.id, message.id):
                    continue
                result = match_post(message.raw_text or "", settings.profile)
                if not result.matched:
                    store.mark(dialog.id, message.id, False)
                    continue
                if sent >= settings.max_notify_per_cycle:
                    log.info("лимит уведомлений за цикл (%s)", settings.max_notify_per_cycle)
                    return sent
                notice = _format_notice(dialog, message, result.reasons, result.score)
                await notify(notice)
                # Mark a match only after successful delivery. Retry failed sends next cycle.
                store.mark(dialog.id, message.id, True)
                sent += 1
                log.info("матч %s / %s (score %s)", dialog.name, message.id, result.score)
        except FloodWaitError as exc:
            log.warning("FloodWait %ss на чате %s", exc.seconds, dialog.name)
            await asyncio.sleep(exc.seconds + 1)
        except RPCError:
            log.exception("ошибка Telegram на чате %s", dialog.name)
        await asyncio.sleep(settings.chat_delay_seconds)

    log.info("цикл: чатов %s, уведомлений %s", dialogs, sent)
    return sent


async def _scan_loop(
    client: TelegramClient,
    settings: Settings,
    store: Store,
    notify: Notify,
    state: ScanState,
    wake_scan: asyncio.Event,
) -> None:
    while True:
        state.running = True
        state.last_sent = 0
        state.cycles += 1
        state.last_started = datetime.now(timezone.utc)
        try:
            count = await scan_once(client, settings, store, notify=notify)
            state.last_sent = count
            state.total_sent += count
            state.last_error = None
        except FloodWaitError as exc:
            state.last_error = f"Telegram просит подождать {exc.seconds} секунд"
            log.warning(state.last_error)
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            state.last_error = type(exc).__name__
            log.exception("сбой цикла сканирования")
        finally:
            state.running = False
            state.last_finished = datetime.now(timezone.utc)
        try:
            await asyncio.wait_for(wake_scan.wait(), timeout=settings.scan_interval_minutes * 60)
        except asyncio.TimeoutError:
            pass
        wake_scan.clear()


async def worker(settings: Settings) -> None:
    store = Store(settings.sqlite_path)
    client = TelegramClient(StringSession(settings.session), settings.api_id, settings.api_hash)
    bot_client: TelegramClient | None = None
    health = await _health_server(settings.port)
    state = ScanState()
    wake_scan = asyncio.Event()

    def request_scan() -> str:
        if state.running:
            return "🔎 Проверка уже выполняется. Результаты придут сюда автоматически."
        if wake_scan.is_set():
            return "🔎 Проверка уже поставлена в очередь."
        wake_scan.set()
        return "🔎 Запускаю внеочередной поиск. Подходящие кастинги пришлю сюда."

    try:
        async with client:
            me = await client.get_me()
            log.info("вошли как %s", getattr(me, "username", None) or me.id)
            owner_id = settings.bot_owner_id or me.id
            if settings.bot_token:
                # Persistent bot session retains the owner's Telegram peer information across restarts.
                bot_session = str(settings.sqlite_path.parent / "casting_bot")
                bot_client = TelegramClient(bot_session, settings.api_id, settings.api_hash)
                BotController(bot_client, owner_id, settings, state, request_scan, client, store).register()
                await bot_client.start(bot_token=settings.bot_token)
                log.info("Telegram-бот запущен для владельца %s", owner_id)
            else:
                log.info("BOT_TOKEN не задан: уведомления пойдут в %s", settings.notify_peer)

            async def notify(text: str) -> None:
                if bot_client is not None:
                    await bot_client.send_message(owner_id, text, parse_mode=None, link_preview=False)
                else:
                    await client.send_message(settings.notify_peer, text, parse_mode=None, link_preview=False)

            await _scan_loop(client, settings, store, notify, state, wake_scan)
    finally:
        if bot_client is not None:
            await bot_client.disconnect()
        health.close()
        await health.wait_closed()


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(worker(settings))


if __name__ == "__main__":
    main()