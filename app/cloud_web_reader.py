"""Opt-in single-instance Telegram Web reader for explicitly approved sources.

Uses the owner's authenticated Telegram Web session in a Playwright Chromium
profile on the Railway volume. No custom Telegram API keys, no bulk discovery,
no automatic joining groups, no exporting browser session to any service.

WARNING: Telegram Web's internal JavaScript API is undocumented. The owner must
explicitly authorize a cloud session; Telegram may restrict automated access.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from app.public_channels import normalize_channel, source_id

LOG = logging.getLogger("casting.cloudweb")
ORIGIN = "https://web.telegram.org/k/"
# Only metadata from the dialog index is used to establish the *exact* username;
# no messages from unapproved chat IDs are ever queried.
INDEX_JS = r"""async (maxDialogs) => {
  const m=window.appImManager?.managers;
  if (!m) throw new Error('Telegram Web session not ready');
  const found=[];
  const seen=new Set();
  for (const filterId of [0,1]) {
    const result=await m.dialogsStorage.getDialogs({filterId,limit:maxDialogs});
    for (const dialog of (result.dialogs || []).slice(0,maxDialogs)) {
      const peer=Number(dialog.peerId);
      if (!Number.isSafeInteger(peer) || peer >= 0 || seen.has(peer)) continue;
      seen.add(peer);
      const chat=await m.appChatsManager.getChat(-peer);
      const username=chat?.username || (chat?.usernames || [])
        .find(x=>x.active || x.pFlags?.active)?.username || '';
      if (username) found.push({username:String(username).toLowerCase(),peer:String(peer)});
    }
  }
  return found;
}"""

HISTORY_JS = r"""async ({peer,limit}) => {
  const m=window.appImManager?.managers;
  if (!m) throw new Error('Telegram Web session not ready');
  const p=Number(peer);
  if (!Number.isSafeInteger(p) || p >= 0) throw new Error('Invalid group peer');
  const page=await m.appMessagesManager.getHistory({peerId:p,limit,offsetId:0});
  const out=[];
  for (const mid of (page.history || [])) {
    const item=await m.appMessagesManager.getMessageByPeer(p,mid);
    if (!item) continue;
    out.push({id:item.mid,date:item.date,text:item.message || ''});
  }
  return out;
}"""


def validate_posts(raw, *, now: int, lookback_hours: int=48, cursor: int=0):
    """Strictly validate only recent messages; no account/contact metadata."""
    if not isinstance(raw,list):
        raise ValueError("Telegram Web вернул некорректные сообщения")
    floor=now-max(1,min(lookback_hours,168))*3600
    unique={}
    for item in raw[:60]:
        if not isinstance(item,dict):
            continue
        try:
            mid=int(item.get("id"))
            ts=int(item.get("date"))
        except (TypeError,ValueError):
            continue
        body=item.get("text") or ""
        if not isinstance(body,str) or not body.strip():
            continue
        if mid<=cursor or mid<=0 or ts<floor or ts>now+300:
            continue
        unique[mid]=(ts,body[:8000])
    return [(mid,*unique[mid]) for mid in sorted(unique)]


def same_account(observed: object, expected: int) -> bool:
    """Fail closed unless the browser supplies exactly the expected user ID."""
    if not isinstance(expected,int) or expected<=0:
        return False
    if isinstance(observed,bool) or not isinstance(observed,(str,int)):
        return False
    value=str(observed)
    return value.isascii() and value.isdecimal() and str(expected)==value


def select_peers(raw, approved: set[str]):
    """Never allow fuzzy title matching or unauthorized group access."""
    if not isinstance(raw,list):
        raise ValueError("Telegram Web не вернул список доступных групп")
    allow={normalize_channel(name) for name in approved}
    result={}
    for item in raw[:400]:
        if not isinstance(item,dict):
            continue
        username=item.get("username")
        peer=item.get("peer")
        if not isinstance(username,str) or not isinstance(peer,str):
            continue
        try:
            valid=normalize_channel(username)
            peer_id=int(peer)
        except (ValueError,TypeError):
            continue
        if valid in allow and peer_id<0:
            result[valid]=str(peer_id)
    return result


class CloudWebReader:
    def __init__(self, db, receiver, *, profile_dir: str,
                 interval_seconds: int=1800, lookback_hours: int=48,\n                 owner_store=None, expected_owner: int=0):
        self.db=db
        self.receiver=receiver
        self.owner_store=owner_store
        self.expected_owner=expected_owner
        self.profile_dir=Path(profile_dir)
        self.interval=max(1800,int(interval_seconds))
        self.lookback_hours=max(1,min(lookback_hours,168))
        self._lock=asyncio.Lock()
        self._page=None
        self._context=None
        self._pw=None
        self.state="выключен"

    async def start(self):
        from playwright.async_api import async_playwright
        self.profile_dir.mkdir(parents=True,exist_ok=True)
        self.profile_dir.chmod(0o700)
        self._pw=await async_playwright().start()
        args=["--no-sandbox"] if hasattr(os,"geteuid") and os.geteuid()==0 else []
        self._context=await self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),headless=True,args=args,
            viewport={"width":1200,"height":900},
            accept_downloads=False,
        )
        self._page=self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(ORIGIN,wait_until="domcontentloaded",timeout=45000)
        self.state="ожидание авторизации"

    async def status(self) -> str:
        if self._page is None:
            return "Браузер не запущен"
        async with self._lock:
            try:
                return await self._page.evaluate("""() => window.appImManager?.managers &&
                  document.querySelector('#column-left') ? 'Авторизован' :
                  document.querySelector('input[type=password]') ? 'Требуется облачный пароль' :
                  document.querySelector('canvas[class*=qrCanvas]') ? 'Ожидает входа по QR' :
                  'Загружается'""")
            except Exception:
                return "Нет связи с браузером"

    async def account_id(self) -> str | None:
        """Use the Web K user manager; absence of a reliable ID is an error."""
        if self._page is None:
            return None
        async with self._lock:
            try:
                value=await self._page.evaluate("""() => {
                  const m=window.appImManager?.managers;
                  const self=m?.appUsersManager?.getSelf?.();
                  return self?.id == null ? null : String(self.id);
                }""")
                return value if isinstance(value,str) and value.isdecimal() else None
            except Exception:
                return None

    async def qr_image(self) -> bytes | None:
        """An ephemeral auth QR, to be sent solely to the configured owner chat."""
        if self._page is None:
            return None
        async with self._lock:
            try:
                node=self._page.locator("canvas[class*=qrCanvas]").first
                if await node.count():
                    return await node.screenshot(timeout=6000)
            except Exception:
                LOG.warning("Login QR unavailable; no session data exposed")
            return None

    async def scan_once(self):
        if self.owner_store is None or not self.expected_owner:
            raise RuntimeError("Owner-scoped storage and verified identity required")
        if not self.owner_store.consented():
            raise RuntimeError("Owner consent required")
        verified=await self.account_id()
        if not same_account(verified,self.expected_owner):
            raise RuntimeError("Telegram Web account does not match owner")
        if self._page is None:
            return
        async with self._lock:
            if not await self._page.evaluate(
                "() => !!(window.appImManager?.managers && document.querySelector('#column-left'))"
            ):
                self.state="ожидание авторизации"
                return
            chosen=[row for row in self.db.public_sources() if row[3] and row[4] and row[5]]
            approved={row[5] for row in chosen}
            self.state="поиск источников"
            # At most 200 dialog summaries. No private message history requested.
            peers=select_peers(await self._page.evaluate(INDEX_JS,200),approved)
            for name,cursor,_,_,_,_,_,_,_ in chosen:
                if name not in peers:
                    self.owner_store.update_source(name,
                        error="Группа отсутствует среди доступных чатов аккаунта",
                        delay=3600)
                    continue
                now=int(time.time())
                try:
                    messages=await self._page.evaluate(
                        HISTORY_JS,{"peer":peers[name],"limit":40}
                    )
                    recent=validate_posts(
                        messages,now=now,lookback_hours=self.lookback_hours,
                        cursor=cursor,
                    )
                    previous=sum(self.owner_store.stats().values())
                    for mid,ts,body in recent:
                        self.owner_store.add_post(name,mid,body,ts)
                    matched=max(0,sum(self.owner_store.stats().values())-previous)
                    self.owner_store.update_source(
                        name,cursor=max((mid for mid,_,_ in recent),default=cursor),
                        visible=len(messages),new=len(recent),delay=self.interval
                    )
                    LOG.info("Cloud Telegram Web %s: visible=%s recent=%s matches=%s",
                             name,len(messages),len(recent),matched)
                except Exception as exc:
                    if "FLOOD_WAIT" in str(exc) or "FLOOD" in str(exc).upper():
                        self.state="Telegram ограничил запросы. Сканирование остановлено"
                        self.db.update_client_status(sid,
                            error="Ограничение Telegram: сканирование остановлено",
                            delay=86400)
                        LOG.error("Telegram Web rate limit; stopping reader")
                        raise RuntimeError("Telegram Web rate limited") from None
                    self.db.update_client_status(sid,
                        error="Не удалось прочитать сообщения из группы",
                        retry_after=3600)
                    LOG.warning("Failed to scan selected source %s",name)
                await asyncio.sleep(3)
            self.state="ожидание следующей проверки"

    async def run(self):
        try:
            await self.start()
            LOG.info("Telegram Web reader initialized; login required if not yet authorized")
            while True:
                if self.owner_store is not None and self.owner_store.consented():\n                    await self.scan_once()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state="ошибка, проверка остановлена"
            LOG.error("Telegram Web reader stopped: %s",type(exc).__name__)
        finally:
            if self._context:
                await self._context.close()
            if self._pw:
                await self._pw.stop()
