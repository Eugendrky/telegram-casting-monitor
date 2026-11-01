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

    async def qr(self):
        if not self.store.consented():
            return 'Сначала подтвердите подключение: /webconnect.'
        await self.start()
        if await self.verified():
            return "Аккаунт уже подключён. /webstatus"
        if await self.reader.status()=="Требуется облачный пароль":
            return "Требуется пароль двухэтапной проверки: откройте /webauth. В боте пароль вводить нельзя."
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
  …3805 tokens truncated…38спользована.'})
                            return
                        work = portal.pilot.submit_password(password)
                        del password
                    else:
                        work = portal.pilot.password_ready()
                    future = asyncio.run_coroutine_threadsafe(work, portal.loop)
                    try:
                        message = future.result(timeout=35)
                    except Exception:
                        future.cancel()
                        self.reply(409, {'message': 'Вход не завершён. Проверьте /webstatus; новая ссылка: /webauth.'})
                        return
                    self.reply(200, {'message': message})
                except (ValueError, UnicodeError):
                    self.reply(400, {'message': 'Некорректный запрос.'})
                finally:
                    body = None  # Do not retain plaintext beyond this request.

        self.server = BoundedServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def link(self):
        return self.origin + '/auth#' + self.tickets.issue()

    def close(self):
        self.tickets.revoke()
        self.server.shutdown()
        self.server.server_close()
