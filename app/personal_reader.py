"""Owner-gated Telegram API QR login and per-user approved-source scanner.

Requires TELEGRAM_API_ID, TELEGRAM_API_HASH, SESSION_ENCRYPTION_KEY.
No password collection, no invitations, no list of account dialogs.
"""
from __future__ import annotations
import asyncio
import io
import logging
import time
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError, RPCError
from telethon.sessions import StringSession
from app.personal_store import PersonalStore
from app.session_cipher import SessionCipher
from app.config import Profile
from app.matcher import match_post
from app.casting_quality import actionable

LOG=logging.getLogger("casting.personal")


class PersonalReader:
    def __init__(self,db,api,*,api_id:int,api_hash:str,key:str,
                 pilot_user_id:int,lookback_hours:int=48,interval:int=1800):
        if api_id<=0 or not api_hash or pilot_user_id<=0:
            raise ValueError("Telegram API credentials and pilot user required")
        self.db,self.api=db,api
        self.api_id,self.api_hash=api_id,api_hash
        self.pilot_user_id=pilot_user_id
        self.vault=SessionCipher(key)
        self.store=PersonalStore(db)
        self.lookback=max(1,min(int(lookback_hours),48))*3600
        self.interval=max(900,int(interval))
        self.pending={}
        self.locks={}
        self.last_qr={}
        self._lock=asyncio.Lock()

    def allowed(self,uid:int)->bool:
        return uid==self.pilot_user_id

    def state(self,uid:int)->str:
        if not self.allowed(uid):
            return "недоступно"
        if self.store.session(uid):
            return "подключён"
        if uid in self.pending:
            return "ожидание QR"
        return "не подключён"

    async def begin(self,uid:int):
        if not self.allowed(uid):
            return "Пилотный вход открыт только владельцу."
        if self.store.session(uid):
            return "Аккаунт уже подключён. /account"
        async with self._lock:
            if uid in self.pending:
                return "QR уже создан. Дождитесь подтверждения или /cancel_login"
            now=time.monotonic()
            if now-self.last_qr.get(uid,0)<90:
                return "Повтор QR возможен через 90 секунд."
            client=TelegramClient(StringSession(),self.api_id,self.api_hash,
                                  flood_sleep_threshold=0)
            try:
                await client.connect()
                qr=await client.qr_login()
            except Exception:
                await client.disconnect()
                LOG.warning("QR login init failed for user=%s",uid)
                return "Не удалось создать QR. Проверьте настройки Telegram API."
            import qrcode
            image=qrcode.make(qr.url)
            buffer=io.BytesIO()
            image.save(buffer,format="PNG")
            self.pending[uid]=client
            self.last_qr[uid]=now
            try:
                await asyncio.to_thread(self.api.send_photo,uid,buffer.getvalue(),
                   "Войти в Telegram: Настройки → Устройства → Подключить устройство. "
                   "Этот QR даёт серверу доступ к аккаунту. Не пересылайте его.")
            except Exception:
                self.pending.pop(uid,None)
                await client.disconnect()
                return "Не удалось отправить QR."
            asyncio.create_task(self._finish(uid,client,qr))
            return "QR отправлен в этот личный чат. Срок действия ограничен."

    async def _finish(self,uid,client,qr):
        try:
            await qr.wait(timeout=90)
            me=await client.get_me()
            if not me or int(me.id)!=int(uid):
                await asyncio.to_thread(self.api.send,uid,
                    "Подключён другой Telegram-аккаунт. Вход отклонён.")
                return
            session=client.session.save()
            if not session:
                raise RuntimeError("No authenticated session")
            self.store.save_session(uid,self.vault.seal(session))
            await asyncio.to_thread(self.api.send,uid,
                "Аккаунт проверен и подключён. "
                "Выбери разрешённый источник через /addmine @username. "
                "После выбора поиск работает раз в 30 минут.")
        except SessionPasswordNeededError:
            await asyncio.to_thread(self.api.send,uid,
                "Telegram требует пароль двухэтапной проверки. "
                "Не отправляйте пароль боту. Вход отменён.")
        except asyncio.TimeoutError:
            await asyncio.to_thread(self.api.send,uid,"Время QR истекло. Попробуйте /connect позже.")
        except Exception as exc:
            LOG.warning("QR login stopped: %s",type(exc).__name__)
            try:
                await asyncio.to_thread(self.api.send,uid,"Не удалось подтвердить вход. Проверьте /account.")
            except Exception:
                pass
        finally:
            async with self._lock:
                if self.pending.get(uid) is client:
                    self.pending.pop(uid,None)
            await client.disconnect()

    async def cancel(self,uid:int):
        client=self.pending.pop(uid,None)
        if client:
            await client.disconnect()
        return "Ожидание QR остановлено."

    async def disconnect(self,uid:int):
        await self.cancel(uid)
        session=self.store.session(uid)
        if session:
            try:
                client=TelegramClient(StringSession(self.vault.open(session)),
                                      self.api_id,self.api_hash)
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        await client.log_out()
                finally:
                    await client.disconnect()
            except Exception as exc:
                LOG.warning("Session revoke failed: %s",type(exc).__name__)
                return ("Не удалось отозвать Telegram-сессию автоматически. "
                        "Завершите сеанс в Telegram → Настройки → Устройства и повторите.")
        self.store.forget(uid)
        return "Локальная сессия и личные объявления удалены. Проверьте устройства Telegram."

    async def scan_user(self,uid:int,*,force:bool=False):
        """Bounded scan of approved public sources; shares no posts with other accounts."""
        if not self.allowed(uid):
            return "Проверка недоступна."
        token=self.store.session(uid)
        if not token:
            return "Сначала подключи аккаунт: /connect"
        now=int(time.time())
        ready=self.store.due_sources(uid,now)
        if not ready:
            return "Новых проверок пока нет. Следующая попытка по расписанию."
        profile=self.db.profile(uid)
        if not profile:
            return "Сначала заполни анкету: /profile"
        client=TelegramClient(StringSession(self.vault.open(token)),
                              self.api_id,self.api_hash,flood_sleep_threshold=0)
        results=[]
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return "Сессия Telegram больше не действует. Отключи её через /disconnect."
            for name,cursor,next_scan,checked,success,error in ready[:3]:
                stamp=int(time.time())
                try:
                    entity=await client.get_input_entity(name)
                    data=[]
                    async for msg in client.iter_messages(entity,limit=80,
                                      **({"min_id":cursor} if cursor else {})):
                        when=int(msg.date.timestamp()) if getattr(msg,"date",None) else 0
                        if when<stamp-self.lookback:
                            continue
                        if when>stamp+300:
                            continue
                        body=(getattr(msg,"raw_text",None) or "").strip()
                        if body and isinstance(msg.id,int) and msg.id>cursor:
                            data.append((msg.id,body,when))
                    for mid,body,when in sorted(data):
                        accepted=actionable(body)[0]
                        fits=False
                        if accepted:
                            person=Profile(city=profile["city"],gender=profile["gender"],
                               age=profile["age"],height_cm=profile["height_cm"],
                               kids_ok=False,include_keywords=[],exclude_keywords=[
                               "набор закрыт","уже набрали","итоги кастинга"])
                            fits=bool(match_post(body,person).matched)
                            self.store.save_casting(uid,name,mid,body,when,matched=fits)
                    maximum=max((mid for mid,_,_ in data),default=cursor)
                    self.store.update_source(uid,name,cursor=maximum,error=None,
                                             interval=self.interval)
                    results.append(f"@{name}: прочитано {len(data)} новых сообщений")
                except FloodWaitError as exc:
                    self.store.update_source(uid,name,error="FLOOD_WAIT",
                                             interval=max(86400,int(exc.seconds)))
                    results.append(f"@{name}: Telegram ограничил запросы")
                    break
                except RPCError as exc:
                    self.store.update_source(uid,name,error=type(exc).__name__,interval=3600)
                    results.append(f"@{name}: источник недоступен")
                except Exception as exc:
                    LOG.warning("personal source read failed user=%s source=%s: %s",
                                uid,name,type(exc).__name__)
                    self.store.update_source(uid,name,error="Ошибка чтения",interval=3600)
                    results.append(f"@{name}: ошибка чтения")
                await asyncio.sleep(3)
        finally:
            await client.disconnect()
        await self.deliver()
        return "\n".join(results) or "Выбранных источников нет. /addmine @username"

    async def deliver(self):
        for uid,username,mid,body,url in self.store.pending():
            if not self.allowed(uid):
                continue
            try:
                await asyncio.to_thread(self.api.send,uid,
                    f"🎬 @{username}\n\n{body[:3200]}\n\n{url}")
                self.store.mark_delivery(uid,username,mid,"sent")
            except Exception as exc:
                code=getattr(exc,"code",0)
                self.store.mark_delivery(uid,username,mid,
                                         "blocked" if code==403 else "pending")

    async def run(self):
        while True:
            try:
                for uid in self.store.connected_users():
                    if self.allowed(uid):
                        await self.scan_user(uid)
                await self.deliver()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Personal reader cycle failed")
            await asyncio.sleep(60)
