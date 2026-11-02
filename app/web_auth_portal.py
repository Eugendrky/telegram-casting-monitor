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

QR_SCRIPT = r"""let token = location.hash.slice(1);
history.replaceState(null, '', '/qr');
const image = document.querySelector('#qr');
const message = document.querySelector('#message');
let timer = null;
let previousUrl = null;
async function refresh() {
  if (!token) { message.textContent = 'Получите новую ссылку командой /webqr в боте.'; return; }
  try {
    const response = await fetch('/auth/qr-image', {method: 'POST', cache: 'no-store',
      credentials: 'omit', headers: {'Authorization': 'Bearer ' + token,
      'Content-Type': 'application/json'}, body: '{}'});
    if (!response.ok) {
      token = '';
      message.textContent = 'Ссылка истекла. Получите новую командой /webqr в боте.';
      return;
    }
    if (response.headers.get('Content-Type').startsWith('image/png')) {
      const nextUrl = URL.createObjectURL(await response.blob());
      image.src = nextUrl;
      image.hidden = false;
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      previousUrl = nextUrl;
      message.textContent = 'Откройте Telegram на телефоне → Настройки → Устройства → Добавить устройство. Код обновляется автоматически.';
    } else {
      const result = await response.json();
      image.hidden = true;
      message.textContent = result.message;
      if (result.state === 'authorized' || result.state === 'password') token = '';
    }
  } catch {
    message.textContent = 'Нет связи с Telegram Web. Страница попробует обновить QR ещё раз.';
  }
  if (token) timer = setTimeout(refresh, 5000);
}
refresh();
"""

QR_PAGE = ("""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход в Telegram Web</title><style>
body{font:17px system-ui;background:#f4f6f9;color:#17212b;margin:0;padding:24px 16px}
main{max-width:440px;margin:6vh auto;background:white;padding:24px;border-radius:16px;text-align:center}
h1{font-size:24px}p{line-height:1.5}img{width:min(80vw,300px);height:auto;margin:12px auto}
</style><main><h1>Подключение Telegram</h1>
<img id="qr" alt="Обновляемый QR для входа в Telegram Web" hidden>
<p id="message" role="status">Проверяем ссылку и загружаем актуальный QR…</p>
<p>Сканируйте только этот код в Telegram на телефоне. Ссылка действует 5 минут. Если Telegram запросит облачный пароль, отправьте боту /webauth.</p>
</main><script>""" + QR_SCRIPT + "</script></html>").encode()

# Only fixed, non-sensitive diagnostics may cross the browser boundary. Never
# return exception text from Telegram, Playwright, or the request handler.
SAFE_AUTH_ERRORS = {
    'Telegram оставил запрос облачного пароля. Проверьте, что он отправлен, и запросите новую ссылку.':
        'Telegram оставил экран облачного пароля. Вход не подтверждён. Получите новую ссылку командой /webauth.',
    'Telegram не подтвердил аккаунт пользователя. Сканирование осталось заблокировано.':
        'Telegram не подтвердил аккаунт пользователя. Сканирование осталось заблокировано; проверьте /webstatus.',
    'Telegram отклонил облачный пароль или оставил запрос активным':
        'Telegram оставил экран облачного пароля. Вход не подтверждён. Проверьте /webstatus и запросите новую ссылку командой /webauth.',
    'Official Telegram Web required':
        'Пароль не отправлен: страница Telegram Web недоступна. Проверьте /webstatus и запросите новую ссылку.',
    'Password prompt is not ready':
        'В Telegram Web сейчас нет активного поля пароля. Проверьте /webstatus и запросите новую ссылку.',
    'Multiple password prompts are visible':
        'Telegram Web показывает несколько полей пароля. Ссылка отозвана, сканирование остаётся заблокировано; проверьте /webstatus.',
    'Telegram login not completed':
        'Telegram Web не подтвердил завершение входа. Проверьте /webstatus и запросите новую ссылку.',
    'Telegram Web account does not match authenticated user':
        'Telegram ID аккаунта не совпал с вашим ID в боте. Вход и сбор остаются заблокированы.',
}

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
    """Per-user one-time tickets retain only digests; restart invalidates all links."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.tokens = {}
        self.by_user = {}
        self.issued = {}

    def issue(self, user_id=0):
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 0:
            raise ValueError("Invalid ticket owner")
        with self.lock:
            now = self.clock()
            issued = [stamp for stamp in self.issued.get(user_id, []) if now - stamp < 600]
            if len(issued) >= 5:
                raise ValueError("Слишком много попыток. Подождите 10 минут.")
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).digest()
            previous = self.by_user.get(user_id)
            if previous is not None:
                self.tokens.pop(previous, None)
            self.tokens[digest] = (user_id, now + 300)
            self.by_user[user_id] = digest
            issued.append(now)
            self.issued[user_id] = issued
            return token

    def valid(self, token, *, consume=False):
        with self.lock:
            if not isinstance(token, str) or not 40 <= len(token) <= 100:
                return False
            digest = hashlib.sha256(token.encode()).digest()
            match = next((key for key in self.tokens if hmac.compare_digest(key, digest)), None)
            ticket = self.tokens.get(match) if match is not None else None
            good = ticket is not None and self.clock() < ticket[1]
            if good and consume:
                user_id, _ = self.tokens.pop(match)
                if self.by_user.get(user_id) == match:
                    self.by_user.pop(user_id, None)
            return good

    def user_id(self, token):
        with self.lock:
            if not isinstance(token, str) or not 40 <= len(token) <= 100:
                return None
            digest = hashlib.sha256(token.encode()).digest()
            match = next((key for key in self.tokens if hmac.compare_digest(key, digest)), None)
            ticket = self.tokens.get(match) if match is not None else None
            if ticket is None or self.clock() >= ticket[1]:
                return None
            return ticket[0]

    def revoke(self, user_id=None):
        with self.lock:
            if user_id is None:
                self.tokens.clear()
                self.by_user.clear()
                return
            digest = self.by_user.pop(user_id, None)
            if digest is not None:
                self.tokens.pop(digest, None)


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

            def reply(self, status, body, content_type='application/json; charset=utf-8', *, script=None):
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
                script_policy = "'none'"
                if script is not None:
                    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                    script_policy = "'sha256-" + digest + "'"
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src " +
                    script_policy + "; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' blob:; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/auth':
                    self.reply(200, PAGE, 'text/html; charset=utf-8', script=SCRIPT)
                elif self.path == '/qr':
                    self.reply(200, QR_PAGE, 'text/html; charset=utf-8', script=QR_SCRIPT)
                elif self.path == '/healthz':
                    self.reply(200, {'status': 'ok'})
                else:
                    self.reply(404, {'message': 'Страница не найдена.'})

            def do_POST(self):
                if self.path not in ('/auth/status', '/auth/password', '/auth/qr-image'):
                    self.reply(404, {'message': 'Страница не найдена.'})
                    return
                if self.headers.get('Origin') != portal.origin:
                    self.reply(403, {'message': 'Недопустимый источник запроса.'})
                    return
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else ''
                if not portal.tickets.valid(token):
                    self.reply(403, {'message': 'Ссылка недействительна или истекла.'})
                    return
                user_id = portal.tickets.user_id(token)
                pilot = portal.pilot_for(user_id)
                if pilot is None:
                    self.reply(403, {'message': 'Ссылка недействительна или истекла.'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if (not 0 < length <= 8192 or self.headers.get('Transfer-Encoding')
                            or self.headers.get('Content-Type') != 'application/json'):
                        raise ValueError()
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError()
                    if self.path == '/auth/qr-image':
                        work = pilot.qr_snapshot()
                    elif self.path == '/auth/password':
                        password = body.get('password')
                        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
                            raise ValueError()
                        if not portal.tickets.valid(token, consume=True):
                            self.reply(403, {'message': 'Ссылка уже использована.'})
                            return
                        work = pilot.submit_password(password)
                        del password
                    else:
                        work = pilot.password_ready()
                    future = asyncio.run_coroutine_threadsafe(work, portal.loop)
                    try:
                        result = future.result(timeout=35)
                    except Exception as exc:
                        future.cancel()
                        message = SAFE_AUTH_ERRORS.get(str(exc),
                            'Запрос не завершён. Проверьте /webstatus и запросите новую ссылку.')
                        self.reply(409, {'message': message})
                        return
                    if self.path == '/auth/qr-image':
                        state, value = result
                        if state == 'qr' and isinstance(value, bytes) and value.startswith(bytes.fromhex('89504e470d0a1a0a')):
                            self.reply(200, value, 'image/png')
                        else:
                            if state == 'authorized':
                                portal.tickets.revoke(user_id)
                            self.reply(200, {'state': state, 'message': value})
                    else:
                        self.reply(200, {'message': result})
                except (ValueError, UnicodeError):
                    self.reply(400, {'message': 'Некорректный запрос.'})
                finally:
                    body = None

        self.server = BoundedServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def pilot_for(self, user_id):
        if user_id is None:
            return None
        if isinstance(getattr(self.pilot, "pilots", None), dict):
            if not self.pilot.is_id_allowed(user_id):
                return None
            return self.pilot.get(user_id, create=False)
        expected = getattr(self.pilot, "owner", None)
        if user_id == 0 or expected == user_id:
            return self.pilot
        return None

    def link(self, user_id=None):
        if user_id is None:
            user_id = getattr(self.pilot, "owner", 0)
        return self.origin + '/auth#' + self.tickets.issue(user_id)

    def qr_link(self, user_id=None):
        if user_id is None:
            user_id = getattr(self.pilot, "owner", 0)
        return self.origin + '/qr#' + self.tickets.issue(user_id)

    def close(self):
        self.tickets.revoke()
        self.server.shutdown()
        self.server.server_close()

    def for_user(self, user_id: int):
        """Return a facade whose links can only mint tickets for one user."""
        portal = self

        class UserPortal:
            class UserTickets:
                @staticmethod
                def revoke():
                    portal.tickets.revoke(user_id)

            tickets = UserTickets()

            def link(self):
                return portal.link(user_id)

            def qr_link(self):
                return portal.qr_link(user_id)

        return UserPortal()
