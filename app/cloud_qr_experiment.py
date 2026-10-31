"""Isolated QR-only Telegram Web login experiment.

Requires its own Telegram bot token, distinct from the production bot. It does
not scan conversations and never exposes session material in replies or logs.
"""
from __future__ import annotations
import asyncio
import io
import logging
import os
import time
from pathlib import Path
from urllib.request import Request,urlopen
import json
import urllib.error

from playwright.async_api import async_playwright

log=logging.getLogger("qr.experiment")
ORIGIN="https://web.telegram.org/k/"
PNG_HEADER=bytes.fromhex("89504e470d0a1a0a")

def request_api(token,method,args=None,photo=None):
    if photo is None:
        payload=json.dumps(args or {},ensure_ascii=False).encode()
        request=Request("https://api.telegram.org/bot"+token+"/"+method,payload,
                        {"Content-Type":"application/json"})
    else:
        import secrets
        boundary="QRBoundary"+secrets.token_hex(8)
        fields=args or {}
        fragments=[]
        for key,value in fields.items():
            fragments.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+
                  key+"\"\r\n\r\n"+str(value)+"\r\n").encode())
        fragments.append(("--"+boundary+"\r\nContent-Disposition: form-data; "+
            "name=\"photo\"; filename=\"login.png\"\r\nContent-Type: image/png\r\n\r\n").encode())
        fragments.append(photo)
        fragments.append(("\r\n--"+boundary+"--\r\n").encode())
        request=Request("https://api.telegram.org/bot"+token+"/"+method,b"".join(fragments),
                        {"Content-Type":"multipart/form-data; boundary="+boundary})
    with urlopen(request,timeout=40) as response:
        result=json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError("Telegram Bot API error")
    return result["result"]

class Login:
    def __init__(self,token,owner,profile):
        self.token=token
        self.owner=owner
        self.profile=profile
        self.ctx=None
        self.page=None
        self.last_qr=0.0
        self.lock=asyncio.Lock()

    async def start(self):
        self.playwright=await async_playwright().start()
        Path(self.profile).mkdir(parents=True,exist_ok=True)
        os.chmod(self.profile,0o700)
        self.ctx=await self.playwright.chromium.launch_persistent_context(
            self.profile,headless=True,accept_downloads=False,
            args=["--disable-dev-shm-usage"],viewport={"width":1280,"height":900})
        self.page=self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        await self.page.goto(ORIGIN,wait_until="domcontentloaded",timeout=60000)
        log.info("Browser initialized. Login requires owner confirmation.")

    async def state(self):
        if not self.page:
            return "Браузер пока не запущен"
        async with self.lock:
            try:
                return await self.page.evaluate("""() => window.appImManager?.managers &&
                    document.querySelector('#column-left') ? 'Авторизация подтверждена' :
                    document.querySelector('input[type=password]') ? 'Telegram требует облачный пароль' :
                    document.querySelector('canvas[class*=qrCanvas]') ? 'QR готов' :
                    'Ожидание загрузки Telegram Web'""")
            except Exception:
                return "Ошибка подключения к браузеру"

    async def respond(self,text):
        state=await self.state()
        if text=="/status":
            return await asyncio.to_thread(request_api,self.token,"sendMessage",
                   {"chat_id":self.owner,"text":state})
        if state=="Авторизация подтверждена":
            return await asyncio.to_thread(request_api,self.token,"sendMessage",
                   {"chat_id":self.owner,"text":"Telegram Web авторизован. Сканирование выключено."})
        if state=="Telegram требует облачный пароль":
            return await asyncio.to_thread(request_api,self.token,"sendMessage",
                   {"chat_id":self.owner,"text":"Telegram запрашивает пароль двухэтапной проверки. "+
                   "Не отправляйте его боту. Испытание остановлено."})
        if time.monotonic()-self.last_qr<20:
            return await asyncio.to_thread(request_api,self.token,"sendMessage",
                   {"chat_id":self.owner,"text":"Повторите запрос QR через 20 секунд."})
        async with self.lock:
            canvas=self.page.locator("canvas[class*=qrCanvas]").first
            if await canvas.count()==0:
                return await asyncio.to_thread(request_api,self.token,"sendMessage",
                    {"chat_id":self.owner,"text":"QR пока не доступен. "+state})
            image=await canvas.screenshot()
        if not image.startswith(PNG_HEADER) or len(image)>800000:
            raise ValueError("Некорректное изображение QR")
        self.last_qr=time.monotonic()
        return await asyncio.to_thread(request_api,self.token,"sendPhoto",
              {"chat_id":self.owner,"caption":"Telegram → Настройки → Устройства → Подключить устройство. "+
               "Никому не пересылайте этот QR."},image)

async def main():
    token=os.getenv("LOGIN_BOT_TOKEN","").strip()
    owner=int(os.getenv("LOGIN_OWNER_ID","0"))
    if not token or owner<=0:
        raise RuntimeError("LOGIN_BOT_TOKEN and LOGIN_OWNER_ID are required")
    login=Login(token,owner,os.getenv("LOGIN_PROFILE_DIR","/data/telegram-web"))
    try:
        await login.start()
        await asyncio.to_thread(request_api,token,"deleteWebhook",{"drop_pending_updates":False})
        offset=0
        while True:
            try:
                updates=await asyncio.to_thread(request_api,token,"getUpdates",
                       {"offset":offset,"timeout":20,"limit":30,"allowed_updates":["message"]})
                for update in updates:
                    offset=max(offset,update["update_id"]+1)
                    msg=update.get("message") or {}
                    sender=(msg.get("from") or {}).get("id")
                    chat=msg.get("chat") or {}
                    if sender!=owner or chat.get("type")!="private":
                        continue
                    cmd=(msg.get("text") or "").strip().lower()
                    if cmd in ("/start","/authorization","authorization","/qr","/status"):
                        await login.respond("/status" if cmd=="/status" else "/qr")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Bot polling issue: %s",type(exc).__name__)
                await asyncio.sleep(15)
    finally:
        if login.ctx: await login.ctx.close()
        if hasattr(login,"playwright"): await login.playwright.stop()

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
