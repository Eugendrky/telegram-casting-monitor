"""Opt-in authorized Telegram client reader for explicitly approved sources.

This worker never joins groups or enumerates account dialogs. It uses only an
authorized user session supplied by the owner, stops on auth/rate-limit errors,
and passes matching posts to the existing Bot API delivery pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, AuthKeyError, UnauthorizedError, RPCError
from telethon.sessions import StringSession

from app.public_channels import source_id

LOG=logging.getLogger("casting.client.scan")


def configured(api_id: str, api_hash: str, session: str) -> bool:
    return bool(api_id and api_hash and session)


async def scan_source(client, db, receiver, username: str, *,
                      lookback_hours: int=48, limit: int=80,
                      interval: int=1800, now: int | None=None):
    """Read recent posts from a chosen source without changing membership."""
    username=username.lower().lstrip("@")
    sid=source_id(username)
    stamp=int(now if now is not None else time.time())
    if not db.should_scan_client(sid,now=stamp):
        return 0
    cursor=db.client_cursor(sid)
    floor=stamp-max(1,min(lookback_hours,168))*3600
    entity=await client.get_input_entity(username)
    messages=[]
    async for msg in client.iter_messages(entity, limit=max(1,min(limit,120)),
                                          **({"min_id":cursor} if cursor else {})):
        if not msg or not isinstance(msg.id,int) or not msg.id:
            continue
        when=int(msg.date.timestamp()) if getattr(msg,"date",None) else 0
        if when and when<floor:
            continue
        if cursor and msg.id<=cursor:
            continue
        messages.append((msg,when))
    new=0
    previously=db.count_source_matches(sid)
    for msg,when in sorted(messages,key=lambda item:item[0].id):
        text=(getattr(msg,"raw_text",None) or "").strip()
        if text:
            receiver.handle_post({
                "chat":{"id":sid,"title":"@"+username,
                        "type":"channel","username":username},
                "message_id":msg.id,"text":text,
                "date":when or stamp,
            })
        # Safe to persist because receiver records/queues synchronously.
        db.advance_client_cursor(sid,msg.id)
        new+=1
    matched=max(0,db.count_source_matches(sid)-previously)
    db.client_scan_completed(sid,scanned=len(messages),new_posts=new,
                             matched=matched,interval=interval,now=stamp)
    LOG.info("Authorized source %s: fetched=%s candidate_posts=%s",
             username,len(messages),matched)
    return len(messages)


async def run_authorized_reader(db,receiver,*,api_id: int,api_hash: str,
                                session: str,interval: int=1800,
                                lookback_hours: int=48,client_factory=TelegramClient):
    if not (api_id and api_hash and session):
        raise ValueError("Для чтения источников требуется собственная авторизованная сессия Telegram")
    interval=max(900,int(interval))
    client=client_factory(StringSession(session),int(api_id),api_hash,
                          flood_sleep_threshold=0)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Авторизация Telegram-клиента не подтверждена")
        LOG.info("Authorized client ready; only approved sources will be queried")
        while True:
            sources=[s for s in db.public_sources() if s[3] and s[4] and s[5]]
            for sid,title,kind,enabled,joined,username in sources:
                if not db.should_scan_client(sid):
                    continue
                try:
                    await scan_source(client,db,receiver,username,
                                      lookback_hours=lookback_hours,
                                      interval=interval)
                except (FloodWaitError,AuthKeyError,UnauthorizedError) as exc:
                    db.update_client_status(sid,error="Доступ Telegram ограничен: "+
                                            type(exc).__name__,
                                            retry_after=86400)
                    LOG.error("Telegram access limited (%s); stopping client reader",
                              type(exc).__name__)
                    return
                except RPCError as exc:
                    db.update_client_status(sid,
                        error="Источник недоступен: "+type(exc).__name__,
                        retry_after=max(interval*2,3600))
                    LOG.warning("Authorized source %s unavailable: %s",
                                username,type(exc).__name__)
                except Exception:
                    db.update_client_status(sid,error="Ошибка проверки источника",
                                            retry_after=3600)
                    LOG.exception("Authorized source failure for %s",username)
                await asyncio.sleep(3)
            await asyncio.sleep(30)
    finally:
        await client.disconnect()
