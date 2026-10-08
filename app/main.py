from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

from app.config import Settings, load_settings
from app.links import message_link
from app.matcher import is_casting_title, match_post
from app.store import Store

log = logging.getLogger("castings")


def _is_target_dialog(dialog: Dialog, scan_mode: str) -> bool:
    if dialog.is_user:
        return False
    if scan_mode == "title":
        return is_casting_title(dialog.name or "")
    return dialog.is_group or dialog.is_channel


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


async def scan_once(client: TelegramClient, settings: Settings, store: Store) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.lookback_hours)
    sent = 0
    dialogs = 0

    async for dialog in client.iter_dialogs():
        if not _is_target_dialog(dialog, settings.scan_mode):
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
                store.mark(dialog.id, message.id, result.matched)
                if not result.matched:
                    continue
                if sent >= settings.max_notify_per_cycle:
                    log.info("лимит уведомлений за цикл (%s), остальное в следующий", settings.max_notify_per_cycle)
                    return sent
                notice = _format_notice(dialog, message, result.reasons, result.score)
                await client.send_message(settings.notify_peer, notice)
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


async def worker(settings: Settings) -> None:
    store = Store(settings.sqlite_path)
    client = TelegramClient(StringSession(settings.session), settings.api_id, settings.api_hash)
    health = await _health_server(settings.port)
    async with client:
        me = await client.get_me()
        log.info("вошли как %s", getattr(me, "username", None) or me.id)
        while True:
            try:
                await scan_once(client, settings, store)
            except FloodWaitError as exc:
                log.warning("глобальный FloodWait %ss", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
            except Exception:
                log.exception("сбой цикла сканирования")
            await asyncio.sleep(settings.scan_interval_minutes * 60)
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
