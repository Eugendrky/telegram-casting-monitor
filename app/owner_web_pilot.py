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
        self.auth_portal=None

    async def start(self):
        async with self._lock:
            if not self.started:
                try:
                    await self.reader.start()
                    self.started=True
                except Exception:
                    if self.reader._context:
                        await self.reader._context.close()
                    if self.reader._pw:
                        await self.reader._pw.stop()
                    self.reader._context=self.reader._pw=self.reader._page=None
                    raise RuntimeError('Telegram Web browser could not start') from None

    async def verified(self):
        return self.started and same_account(await self.reader.account_id(),self.owner)

    async def status(self):
        if not self.started:
            return "Браузер выключен"
        if await self.verified():
            return "Telegram Web: владелец подтверждён. /webscan"
        state=await self.reader.status()
        return ("Telegram Web: "+state+". Сканирование заблокировано."+
                (" Откройте /webauth для двухэтапного пароля." if state=="Требуется облачный пароль" else ""))

    async def password_ready(self):
        async with self._lock:
            if not self.store.consented() or not self.started:
                raise ValueError('Owner consent required')
            if await self.reader.status() != 'Требуется облачный пароль':
                raise ValueError('Password prompt required')
            return 'Введите пароль для завершения входа. После отправки ссылка станет недействительной.'

    async def submit_password(self, password):
        async with self._lock:
            if not self.store.consented() or not self.started:
                raise ValueError('Owner consent required')
            if await self.reader.status() != 'Требуется облачный пароль':
                raise ValueError('Password prompt required')
            await self.reader.submit_password(password)
            password = ''
            if not await self.verified():
                raise ValueError('Owner identity not confirmed')
            return 'Вход завершён, владелец подтверждён. Вернитесь в бота: /webstatus.'

    async def auth_link(self):
        if self.auth_portal is None:
            return 'Страница ввода пароля пока не настроена.'
        await self.password_ready()
        return ('Вводите облачный пароль только на этой странице, не в чате. '
                'Одноразовая ссылка действует 5 минут:\n'+self.auth_portal.link())

    async def qr_snapshot(self):
        """Return only the current official Telegram QR, never account data."""
        if not self.store.consented() or not self.started:
            return 'unavailable', 'Сначала подтвердите подключение в боте.'
        if await self.verified():
            return 'authorized', 'Вход подтверждён. Вернитесь в бот и выполните /webstatus.'
        state=await self.reader.status()
        if state == 'Требуется облачный пароль':
            return 'password', 'Telegram запросил облачный пароль. Отправьте боту /webauth.'
        if state != 'Ожидает входа по QR':
            return 'loading', 'Telegram Web ещё загружается. Код обновится автоматически.'
        png=await self.reader.qr_image()
        if not png or not png.startswith(bytes.fromhex('89504e470d0a1a0a')):
            return 'loading', 'Telegram обновляет код. Подождите несколько секунд.'
        return 'qr', png

    async def qr(self):
        if not self.store.consented():
            return 'Сначала подтвердите подключение: /webconnect.'
        await self.start()
        if await self.verified():
            return "Аккаунт уже подключён. /webstatus"
        if await self.reader.status()=="Требуется облачный пароль":
            return "Требуется пароль двухэтапной проверки: откройте /webauth. В боте пароль вводить нельзя."
        if self.auth_portal is not None:
            link=self.auth_portal.qr_link()
            return ('Откройте ссылку на компьютере и отсканируйте показанный там обновляемый QR '
                    'в Telegram на телефоне → Настройки → Устройства → Добавить устройство. '
                    'Ссылка действует 5 минут:\n'+link)
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
        if self.store.consented():
            try:
                await self.start()
            except Exception:
                LOG.warning('Stored Web profile could not start; use /webqr to retry')
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
        async with self._lock, self._scan_lock:
            if self.auth_portal:
                self.auth_portal.tickets.revoke()
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
