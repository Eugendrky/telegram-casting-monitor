from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError, AuthKeyError, UnauthorizedError
from telethon.tl.types import InputPeerChannel, InputPeerChat
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

from app.bot import BotController
from app.client import user_client
from app.config import Settings, load_settings
from app.control import ScanState
from app.links import message_link
from app.matcher import match_post
from app.ai import review_casting
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


def _selected_peer(source):
    chat_id, title, peer_id, access_hash, peer_kind, username = source
    if peer_kind == "channel" and peer_id and access_hash is not None:
        return InputPeerChannel(peer_id, access_hash)
    if peer_kind == "chat" and peer_id:
        return InputPeerChat(peer_id)
    return username or None


async def _scan_entity(client, entity, chat_id: int, name: str, settings: Settings,
                       store: Store, notify: Notify, sent: int) -> int:
    """Read only recent posts from an explicitly selected source, with a durable cursor."""
    cursor = store.cursor(chat_id)
    first_run = cursor == 0
    pending = []
    # The first read is deliberately bounded and not a full historical backfill.
    async for message in client.iter_messages(
        entity, limit=settings.messages_per_chat,
        **({} if first_run else {"min_id": cursor, "reverse": True}),
    ):
        if message.date is None:
            continue
        if first_run and message.date < datetime.now(timezone.utc) - timedelta(hours=settings.lookback_hours):
            break
        pending.append(message)
    if first_run:
        pending.reverse()
    for message in pending:
        if sent >= settings.max_notify_per_cycle:
            break  # Do not advance past unprocessed messages.
        if store.is_seen(chat_id, message.id):
            store.advance_cursor(chat_id, message.id)
            continue
        result = match_post(message.raw_text or "", settings.profile)
        if result.matched:
            review = await review_casting(message.raw_text or "", settings)
            if review is not None:
                if not review["is_casting"] or not review["fits_profile"]:
                    store.mark(chat_id, message.id, False)
                    store.advance_cursor(chat_id, message.id)
                    continue
                result.reasons.append("ИИ: " + review["reason"])
            notice = _format_notice(type("Source", (), {"name": name})(),
                                    message, result.reasons, result.score)
            await notify(notice)
            sent += 1
        store.mark(chat_id, message.id, result.matched)
        store.advance_cursor(chat_id, message.id)
    return sent


async def scan_once(client: TelegramClient, settings: Settings, store: Store,
                    notify: Notify | None = None) -> int:
    if not settings.scan_enabled:
        log.info("scan disabled: set SCAN_ENABLED=true after account access is restored")
        return 0
    if notify is None:
        async def notify(text: str) -> None:
            await client.send_message(settings.notify_peer, text, parse_mode=None, link_preview=False)
    sources = store.selected_sources()
    sent = 0
    for source in sources:
        chat_id, name = source[:2]
        peer = _selected_peer(source)
        if peer is None:
            log.warning("source %s has no cached peer; select it again via /sources", chat_id)
            continue
        try:
            sent = await _scan_entity(client, peer, chat_id, name, settings, store, notify, sent)
        except (FloodWaitError, AuthKeyError, UnauthorizedError):
            raise  # STOP; never hammer Telegram when access is restricted.
        except RPCError as exc:
            log.warning("source %s unavailable: %s", chat_id, type(exc).__name__)
        if sent >= settings.max_notify_per_cycle:
            break
        await asyncio.sleep(settings.chat_delay_seconds)
    log.info("cycle: selected sources %s, notices %s", len(sources), sent)
    return sent


async def _scan_loop(client: TelegramClient, settings: Settings, store: Store,
                     notify: Notify, state: ScanState, wake_scan: asyncio.Event) -> None:
    while True:
        if state.paused:
            log.error("Monitoring paused: manual inspection is required")
            return
        state.running = True
        state.last_sent = 0
        state.cycles += 1
        state.last_started = datetime.now(timezone.utc)
        try:
            count = await scan_once(client, settings, store, notify=notify)
            state.last_sent = count
            state.total_sent += count
            state.last_error = None
        except (FloodWaitError, AuthKeyError, UnauthorizedError) as exc:
            state.last_error = f"Telegram ограничил доступ: {type(exc).__name__}"
            state.paused = True
            log.error("Telegram access restricted: %s. Stopping scanner.", type(exc).__name__)
        except Exception as exc:
            state.last_error = type(exc).__name__
            state.paused = True
            log.exception("Scanning failed; stopping until the cause is inspected")
        finally:
            state.running = False
            state.last_finished = datetime.now(timezone.utc)
        if state.paused:
            return
        try:
            await asyncio.wait_for(wake_scan.wait(), timeout=settings.scan_interval_minutes * 60)
        except asyncio.TimeoutError:
            pass
        wake_scan.clear()


async def worker(settings: Settings) -> None:
    store = Store(settings.sqlite_path)
    client = user_client(settings)
    bot_client: TelegramClient | None = None
    health = await _health_server(settings.port) if settings.port > 0 else None
    state = ScanState()
    wake_scan = asyncio.Event()

    def request_scan() -> str:
        if not settings.scan_enabled:
            return "⏸ Сканирование выключено. Проверьте доступ к аккаунту и SCAN_ENABLED."
        if state.paused:
            return "⛔ Telegram ограничил доступ или произошла ошибка. Изучите журнал и устраните причину."
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

            if settings.scan_enabled:
                await _scan_loop(client, settings, store, notify, state, wake_scan)
            else:
                log.warning("SCAN_ENABLED=false. Monitoring stopped by default.")
                if bot_client is not None:
                    await bot_client.run_until_disconnected()
    finally:
        store.close()
        if bot_client is not None:
            await bot_client.disconnect()
        if health is not None:
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
