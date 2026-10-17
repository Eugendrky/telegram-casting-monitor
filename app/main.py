from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

from app.bot import BotController
from app.client import user_client
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


async def _scan_entity(
    client: TelegramClient,
    entity,
    name: str,
    settings: Settings,
    store: Store,
    notify: Notify,
    cutoff: datetime,
    sent: int,
) -> int:
    try:
        async for message in client.iter_messages(entity, limit=settings.messages_per_chat):
            if message.date is None:
                continue
            msg_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
            if msg_date < cutoff:
                break
            chat_id = getattr(entity, "id", 0)
            if store.is_seen(chat_id, message.id):
                continue
            result = match_post(message.raw_text or "", settings.profile)
            if not result.matched:
                store.mark(chat_id, message.id, False)
                continue
            if sent >= settings.max_notify_per_cycle:
                log.info("лимит уведомлений за цикл (%s)", settings.max_notify_per_cycle)
                return sent
            notice = _format_notice(type("D", (), {"name": name})(), message, result.reasons, result.score)
            await notify(notice)
            store.mark(chat_id, message.id, True)
            sent += 1
            log.info("матч %s / %s (score %s)", name, message.id, result.score)
    except FloodWaitError as exc:
        log.warning("FloodWait %ss на чате %s", exc.seconds, name)
        await asyncio.sleep(exc.seconds + 1)
    except RPCError:
        log.exception("ошибка Telegram на чате %s", name)
    await asyncio.sleep(settings.chat_delay_seconds)
    return sent


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

    source_mode = store.source_mode()
    chosen_sources = store.selected_source_ids() if source_mode == "selected" else set()

    # 1. Обычные диалоги
    async for dialog in client.iter_dialogs():
        if not source_allowed(dialog, settings.scan_mode, source_mode, chosen_sources):
            continue
        dialogs += 1
        sent = await _scan_entity(client, dialog.entity, dialog.name or "?", settings, store, notify, cutoff, sent)
        if sent >= settings.max_notify_per_cycle:
            break

    # 2. Вручно добавленные по username
    if source_mode == "selected" or not chosen_sources:
        for username, title, _ in store.custom_sources():
            try:
                entity = await client.get_entity(username)
                name = getattr(entity, "title", None) or title or username
                dialogs += 1
                sent = await _scan_entity(client, entity, name, settings, store, notify, cutoff, sent)
                if sent >= settings.max_notify_per_cycle:
                    break
            except Exception as exc:
                log.warning("не удалось открыть @%s: %s", username, exc)

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
    client = user_client(settings)
    bot_client: TelegramClient | None = None
    health = await _health_server(settings.port) if settings.port > 0 else None
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
            if settings.used_official_api:
                log.info("api_id взят из открытого исходника Telegram Desktop")
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

            await _scan_loop(client, settings, store, notify, state, wake_scan)
    finally:
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
