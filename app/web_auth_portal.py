"""Short-lived owner login bridge. No passwords, tokens or requests are logged."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


SCRIPT = r"""let token = location.hash.slice(1);
history.replaceState(null, '', '/auth');
const form = document.querySelector('form');
const message = document.querySelector('#message');
const field = document.querySelector('input');
const button = document.querySelector('button');
async function request(path, body) {
  const response = await fetch(path, {method: 'POST', cache: 'no-store',
    credentials: 'omit', headers: {'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = await response.json();
  message.textContent = result.message;
  return response.ok;
}
form.addEventListener('submit', async event => {
  event.preventDefault(); button.disabled = true;
  const password = field.value; field.value = '';
  try { await request('/auth/password', {password}); }
  catch { message.textContent = 'Нет связи. Проверьте /webstatus в боте.'; }
  finally { token = ''; field.disabled = true; }
});
if (token) {
  request('/auth/status', {}).then(ok => {button.disabled = !ok; field.disabled = !ok;})
    .catch(() => {message.textContent = 'Нет связи с сервером.';});
} else {message.textContent = 'Получите новую ссылку командой /webauth в боте.';}
"""

PAGE = ("""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение Telegram</title><style>
body{font:17px system-ui;background:#f4f6f9;color:#17212b;margin:0;padding:32px 16px}
main{max-width:440px;margin:8vh auto;background:white;padding:28px;border-radius:16px}
h1{font-size:25px}input,button{box-sizing:border-box;width:100%;padding:14px;font:inherit;margin:12px 0}
button{background:#1675bf;color:white;border:0;border-radius:8px}button:disabled{opacity:.5}
p{line-height:1.5}label{display:block}#message{min-height:48px}
</style><main><h1>Двухэтапная защита Telegram</h1>
<p>Страница вашего кастинг-бота. Пароль передаётся Telegram Web на сервере
для завершения входа. В базе и журналах приложения он не сохраняется.</p>
<form autocomplete="off"><label for="password">Облачный пароль Telegram</label>
<input id="password" type="password" autocomplete="off" required maxlength="1024" disabled>
<button disabled>Завершить вход</button></form>
<p id="message" role="status">Проверяем одноразовую ссылку…</p>
<p>Один ввод на ссылку, срок действия 5 минут. После входа проверьте /webstatus
в боте. Отключение: /webdisconnect и Telegram → Настройки → Устройства.</p>
</main><script>""" + SCRIPT + "</script></html>").encode()


class LoginTickets:
    """Only a digest is retained in memory; restart/revoke invalidates all links."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.digest = None
        self.deadline = 0
        self.issued = []

    def issue(self):
        with self.lock:
            now = self.clock()
            self.issued = [stamp for stamp in self.issued if now - stamp < 600]
            if len(self.issued) >= 5:
                raise ValueError("Слишком много попыток. Подождите 10 минут.")
            token = secrets.token_urlsafe(32)
            self.digest = hashlib.sha256(token.encode()).digest()
            self.deadline = now + 300
            self.issued.append(now)
            return token

    def valid(self, token, *, consume=False):
        with self.lock:
            if not isinstance(token, str) or not 40 <= len(token) <= 100:
                return False
            good = (self.digest is not None and self.clock() < self.deadline
                    and hmac.compare_digest(self.digest, hashlib.sha256(token.encode()).digest()))
            if good and consume:
                self.digest = None
            return good

    def revoke(self):
        with self.lock:
            self.digest = None


def trusted_origin(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or parsed.port not in (None, 443)):
        raise ValueError('WEB_AUTH_BASE_URL must be an HTTPS origin')
    return 'https://' + parsed.netloc.lower()


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        pass  # Never dump requests, credentials or exception arguments.


class AuthPortal:
    def __init__(self, pilot, base_url, loop, *, host='0.0.0.0', port=8080):
        self.pilot = pilot
        self.origin = trusted_origin(base_url)
        self.loop = loop
        self.tickets = LoginTickets()
        portal = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'CastingAuth'
            sys_version = ''

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass

            def reply(self, status, body, content_type='application/json; charset=utf-8'):
                if isinstance(body, dict):
                    body = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
                digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'sha256-" +
                    digest + "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/auth':
                    self.reply(200, PAGE, 'text/html; charset=utf-8')
                elif self.path == '/healthz':
                    self.reply(200, {'status': 'ok'})
                else:
                    self.reply(404, {'message': 'Страница не найдена.'})

            def do_POST(self):
                if self.path not in ('/auth/status', '/auth/password'):
                    self.reply(404, {'message': 'Страница не найдена.'})
                    return
                if self.headers.get('Origin') != portal.origin:
                    self.reply(403, {'message': 'Недопустимый источник запроса.'})
                    return
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else ''
                if not portal.tickets.valid(token):
                    self.reply(403, {'message': 'Ссылка недействительна. Получите новую: /webauth.'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if (not 0 < length <= 8192 or self.headers.get('Transfer-Encoding')
                            or self.headers.get('Content-Type') != 'application/json'):
                        raise ValueError()
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError()
                    if self.path == '/auth/password':
                        password = body.get('password')
                        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
                            raise ValueError()
                        if not portal.tickets.valid(token, consume=True):
                            self.reply(403, {'message': 'Ссылка уже использована.'})
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

