"""Small synchronous Bot API client executed outside the event loop."""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        self.code = code
        self.description = description
        self.retry_after = retry_after
        super().__init__(f"Telegram Bot API {code}: {description}")


class BotAPI:
    def __init__(self, token: str, timeout: int = 35):
        if not token:
            raise ValueError("BOT_TOKEN required")
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.timeout = timeout

    def call(self, method: str, **params):
        request = urllib.request.Request(
            self.base_url + method,
            data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type":"application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except (ValueError, OSError):
                raise TelegramError(exc.code, "HTTP error") from exc
        if not result.get("ok"):
            retry = result.get("parameters",{}).get("retry_after",0)
            raise TelegramError(result.get("error_code",0),
                                result.get("description","unknown Telegram error"),retry)
        return result.get("result")

    def get_updates(self, offset: int):
        return self.call("getUpdates", offset=offset, timeout=20, limit=40,
                         allowed_updates=["message","channel_post","my_chat_member",
                                          "edited_message","edited_channel_post",
                                          "callback_query"])
    def send(self, chat_id: int, text: str, reply_markup=None):
        params = {"chat_id":chat_id,"text":text[:3900],"disable_web_page_preview":True}
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", **params)

    def send_photo(self, chat_id: int, png: bytes, caption: str = ""):
        """Upload a short-lived QR image only to the caller-supplied chat.

        The login token stays out of logs, source control, and text messages.
        """
        if not png or len(png)>1_000_000 or not png.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
            raise ValueError("Expected a small PNG image")
        boundary="CastingBot"+secrets.token_hex(12)
        prefix=(
            "--"+boundary+"\\r\\n"
            'Content-Disposition: form-data; name="chat_id"\\r\\n\\r\\n'
            +str(chat_id)+"\\r\\n"
            "--"+boundary+"\\r\\n"
            'Content-Disposition: form-data; name="caption"\\r\\n\\r\\n'
            +caption[:200]+"\\r\\n"
            "--"+boundary+"\\r\\n"
            'Content-Disposition: form-data; name="photo"; filename="login.png"\\r\\n'
            "Content-Type: image/png\\r\\n\\r\\n"
        ).replace("\\r","\r").replace("\\n","\n").encode("utf-8")
        suffix=("\r\n--"+boundary+"--\r\n").encode("ascii")
        request=urllib.request.Request(
            self.base_url+"sendPhoto",
            data=prefix+png+suffix,
            headers={"Content-Type":"multipart/form-data; boundary="+boundary},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                result=json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                result=json.loads(exc.read())
            except (ValueError,OSError):
                raise TelegramError(exc.code,"Не удалось отправить QR") from exc
        if not result.get("ok"):
            raise TelegramError(result.get("error_code",0),
                                result.get("description","Ошибка отправки QR"))
        return result.get("result")
