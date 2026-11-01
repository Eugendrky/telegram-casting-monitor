"""Owner-only Web K connection, guarded scanning and private delivery."""
from __future__ import annotations
import asyncio
import logging
import shutil
from pathlib import Path

from app.cloud_web_reader import CloudWebReader,same_account
from app.web_owner_store import WebOwnerStore

LOG=logging.getLogger("casting.webpilot")

class OwnerWebPilot:
    def __init__(self,db,api,owner_id:int,*,profile_dir="/data/telegram-web-owner"):
        if owner_id<=0 or Path(profile_dir).name!="telegram-web-owner":
            raise ValueError("Owner and isolated browser path required")
        self.owner=owner_id
        self.db=db
        self.api=api
        self.store=WebOwnerStore(db,owner_id)
        self.reader=CloudWebReader(db,None,profile_dir=profile_dir,
            owner_store=self.store,expected_owner=owner_id)
        self._lock=asyncio.Lock()
        self._scan_lock=asyncio.Lock()
        self.started=False

    async def start(self):
        async with self._lock:
            if not self.started:
                await self.reader.start()
                self.started=True

    async def verified(self):
        return self.started and same_account(await self.reader.account_id(),self.owner)

    async def status(self):
        if not self.started:
            return "Браузер выключен"
        if await self.verified():
            return "Telegram Web: владелец подтверждён. /webscan"
        return "Telegram Web: "+await self.reader.status()+". Сканирование заблокировано."

    async def qr(self):
        await self.start()
        if await self.verified():
            return "Аккаунт уже подключён. /webstatus"
        if await self.reader.status()=="Требуется облачный пароль":
            return "Требуется пароль двухэтапной проверки: в боте его вводить нельзя."
        png=await self.reader.qr_image()
        if not png or not png.startswith(bytes.fromhex("89504e470d0a1a0a")):
            return "QR пока недоступен; повторите /webqr."
        await asyncio.to_thread(self.api.send_photo,self.owner,png,
            "Вход в Telegram Web на сервере. Telegram → Настройки → Устройства. Не пересылайте QR.")
        return "QR отправлен. После сканирования: /webstatus."

    async def scan(self):
        if not self.store.consented():
            return "Сначала подтвердите подключение: /webconnect."
        if not await self.verified():
            return "Сканирование остановлено: аккаунт Telegram не подтверждён."
        async with self._scan_lock:
            await self.reader.scan_once()
            await self.deliver()
        status=[]
        for name,cursor,next_scan,checked,success,error,visible,new,truncated in self.store.sources():
            status.append(f"@{name}: "+(error or f"проверено {visible}, новых {new}"))
        return "\n".join(status) if status else "Нет выбранных источников. /websource @moscowfilms"

    async def deliver(self):
        if not await self.verified():
            return
        for _ in range(12):
            item=self.store.claim_next()
            if not item:
                break
            name,mid,body,ts=item
            try:
                await asyncio.to_thread(self.api.send,self.owner,
                    f"🎬 @{name}\n\n{body[:3100]}\n\nhttps://t.me/{name}/{mid}",
                    reply_markup={"inline_keyboard":[[
                        {"text":"👍 Подходит","callback_data":f"webfb:yes:{name}:{mid}"},
                        {"text":"👎 Не подходит","callback_data":f"webfb:no:{name}:{mid}"}]]})
            except Exception as exc:
                self.store.mark_delivery(name,mid,
                    "blocked" if getattr(exc,"code",0)==403 else "retry")
            else:
                self.store.mark_delivery(name,mid,"sent")

    async def run(self):
        while True:
            try:
                if self.store.consented() and await self.verified():
                    await self.scan()
                elif self.started and not await self.verified():
                    LOG.info("Web owner scan paused: identity not verified")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Owner Web scan paused: %s",type(exc).__name__)
            await asyncio.sleep(60)

    async def disconnect(self):
        async with self._scan_lock:
            if self.reader._context:
                await self.reader._context.close()
                self.reader._context=None
            if self.reader._pw:
                await self.reader._pw.stop()
                self.reader._pw=None
            self.reader._page=None
            self.started=False
            self.store.forget()
            profile=self.reader.profile_dir
            if profile.name=="telegram-web-owner" and profile.is_dir():
                shutil.rmtree(profile)
        return ("Локальная сессия и личные данные удалены. "
                "Завершите сеанс также в Telegram → Настройки → Устройства.")
