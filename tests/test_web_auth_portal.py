import asyncio
import base64
import hashlib
import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from app.bot_db import Database
from app.owner_web_pilot import OwnerWebPilot
from app.web_auth_portal import AuthPortal, LoginTickets, QR_SCRIPT, trusted_origin


class TicketTests(unittest.TestCase):
    def test_rotation_expiry_revocation_and_one_use(self):
        clock = [0]
        tickets = LoginTickets(lambda: clock[0])
        first = tickets.issue()
        self.assertTrue(tickets.valid(first))
        second = tickets.issue()
        self.assertFalse(tickets.valid(first))
        self.assertTrue(tickets.valid(second, consume=True))
        self.assertFalse(tickets.valid(second, consume=True))
        expired = tickets.issue()
        clock[0] = 300
        self.assertFalse(tickets.valid(expired))
        revoked = tickets.issue()
        tickets.revoke()
        self.assertFalse(tickets.valid(revoked))

    def test_rate_limit_and_origin_validation(self):
        tickets = LoginTickets(lambda: 1)
        for _ in range(5):
            tickets.issue()
        with self.assertRaises(ValueError):
            tickets.issue()
        for url in ('http://bot.test', 'https://user:pass@bot.test',
                    'https://bot.test/auth', 'https://bot.test#secret',
                    'https://bot.test?x=1', 'https://bot.test:8443'):
            with self.assertRaises(ValueError):
                trusted_origin(url)
        self.assertEqual(trusted_origin('https://bot.test/'), 'https://bot.test')

    def test_ticket_revocation_is_scoped_to_one_user(self):
        tickets = LoginTickets()
        first = tickets.issue(101)
        second = tickets.issue(202)
        self.assertEqual(tickets.user_id(first), 101)
        self.assertEqual(tickets.user_id(second), 202)
        tickets.revoke(101)
        self.assertFalse(tickets.valid(first))
        self.assertTrue(tickets.valid(second))


class PortalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pilot = Mock()
        self.pilot.password_ready = AsyncMock(return_value='Готово')
        self.pilot.submit_password = AsyncMock(return_value='Вход завершён')
        self.pilot.qr_snapshot = AsyncMock(return_value=(
            'qr', bytes.fromhex('89504e470d0a1a0a') + b'test-image'))
        self.portal = AuthPortal(self.pilot, 'https://bot.test',
            asyncio.get_running_loop(), host='127.0.0.1', port=0)
        self.portal.start()
        self.port = self.portal.server.server_port

    async def asyncTearDown(self):
        await asyncio.to_thread(self.portal.close)

    async def request(self, path, token='', body=None, origin='https://bot.test', method='POST'):
        def send():
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            headers = {'Origin': origin, 'Authorization': 'Bearer '+token,
                       'Content-Type': 'application/json'}
            conn.request(method, path, json.dumps(body or {}), headers)
            response = conn.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            conn.close()
            return result
        return await asyncio.to_thread(send)

    async def test_unauthorized_and_foreign_origin_cannot_submit(self):
        token = self.portal.tickets.issue()
        for candidate, origin in (('', 'https://bot.test'),
                                  (token, 'https://attacker.test')):
            status, _, _ = await self.request('/auth/password', candidate,
                {'password': 'private-test'}, origin)
            self.assertEqual(status, 403)
        self.pilot.submit_password.assert_not_awaited()

    async def test_status_does_not_consume_password_is_one_time(self):
        token = self.portal.tickets.issue()
        self.assertEqual((await self.request('/auth/status', token))[0], 200)
        self.assertEqual((await self.request('/auth/password', token,
                         {'password': 'private-test'}))[0], 200)
        self.assertEqual((await self.request('/auth/password', token,
                         {'password': 'private-test'}))[0], 403)
        self.pilot.submit_password.assert_awaited_once_with('private-test')

    async def test_errors_are_redacted_and_consume_ticket(self):
        token = self.portal.tickets.issue()
        self.pilot.submit_password.side_effect = ValueError('private-test')
        status, _, body = await self.request('/auth/password', token,
                                            {'password': 'private-test'})
        self.assertEqual(status, 409)
        self.assertNotIn(b'private-test', body)
        self.assertNotIn(token.encode(), body)
        self.assertFalse(self.portal.tickets.valid(token))

    async def test_safe_auth_diagnostic_explains_password_prompt_state(self):
        token = self.portal.tickets.issue()
        self.pilot.submit_password.side_effect = ValueError(
            'Telegram оставил запрос облачного пароля. Проверьте, что он отправлен, и запросите новую ссылку.')
        status, _, body = await self.request('/auth/password', token,
                                            {'password': 'private-test'})
        self.assertEqual(status, 409)
        self.assertIn('Telegram оставил экран облачного пароля'.encode(), body)
        self.assertNotIn('private-test'.encode(), body)

    async def test_page_has_no_secret_and_security_headers(self):
        token = self.portal.tickets.issue()
        status, headers, body = await self.request('/auth', method='GET')
        self.assertEqual(status, 200)
        self.assertNotIn(token.encode(), body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertIn("script-src 'sha256-", headers['Content-Security-Policy'])
        self.assertIn(b'history.replaceState', body)
        self.assertEqual((await self.request('/auth?token='+token, method='GET'))[0], 404)

    async def test_live_qr_page_and_image_endpoint_are_protected_and_refreshable(self):
        token = self.portal.tickets.issue()
        status, headers, body = await self.request('/qr', method='GET')
        self.assertEqual(status, 200)
        self.assertNotIn(token.encode(), body)
        self.assertIn(b'/auth/qr-image', body)
        self.assertIn(b'setTimeout(refresh, 5000)', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn(base64.b64encode(hashlib.sha256(QR_SCRIPT.encode()).digest()).decode(),
                      headers['Content-Security-Policy'])
        self.assertEqual((await self.request('/auth/qr-image'))[0], 403)
        self.assertEqual((await self.request('/auth/qr-image', token,
                         origin='https://attacker.test'))[0], 403)
        first = await self.request('/auth/qr-image', token)
        second = await self.request('/auth/qr-image', token)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]['Content-Type'], 'image/png')
        self.assertEqual(first[2], second[2])
        self.assertTrue(self.portal.tickets.valid(token))
        self.assertEqual(self.pilot.qr_snapshot.await_count, 2)

    async def test_authorized_qr_refresh_revokes_ticket(self):
        token = self.portal.tickets.issue()
        self.pilot.qr_snapshot.return_value = (
            'authorized', 'Вход подтверждён.')
        status, _, body = await self.request('/auth/qr-image', token)
        self.assertEqual(status, 200)
        self.assertIn('authorized', body.decode())
        self.assertFalse(self.portal.tickets.valid(token))

    async def test_invalid_payload_rejected_without_login(self):
        token = self.portal.tickets.issue()
        for body in ({'password': ''}, {'password': 123}, {'password': 'x'*1025}):
            self.assertEqual((await self.request('/auth/password', token, body))[0], 400)
        self.pilot.submit_password.assert_not_awaited()


class PilotAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.folder.name)/'test.db')
        self.pilot = OwnerWebPilot(self.db, Mock(), 101,
            profile_dir=str(Path(self.folder.name)/'telegram-web-owner'))
        self.pilot.started = True
        self.pilot.reader.status = AsyncMock(return_value='Требуется облачный пароль')
        self.pilot.reader.submit_password = AsyncMock()
        self.pilot.reader.account_id = AsyncMock(return_value='101')

    async def asyncTearDown(self):
        self.db.close()
        self.folder.cleanup()

    async def test_consent_and_exact_account_required(self):
        with self.assertRaises(ValueError):
            await self.pilot.submit_password('private-test')
        self.pilot.reader.submit_password.assert_not_awaited()
        self.pilot.store.consent()
        self.pilot.reader.account_id.return_value = '102'
        with self.assertRaises(ValueError):
            await self.pilot.submit_password('private-test')
        self.pilot.reader.account_id.return_value = '101'
        self.assertIn('подтверждён', await self.pilot.submit_password('private-test'))

    async def test_revoke_on_disconnect_blocks_outstanding_password(self):
        self.pilot.store.consent()
        self.pilot.auth_portal = Mock()
        await self.pilot.disconnect()
        self.pilot.auth_portal.tickets.revoke.assert_called_once()
        with self.assertRaises(ValueError):
            await self.pilot.submit_password('private-test')
        self.pilot.reader.submit_password.assert_not_awaited()

    async def test_qr_requires_consent_even_if_called_directly(self):
        self.assertIn('/webconnect', await self.pilot.qr())

    async def test_qr_prefers_live_owner_link_over_stale_photo(self):
        self.pilot.store.consent()
        self.pilot.auth_portal = Mock()
        self.pilot.auth_portal.qr_link.return_value = 'https://bot.test/qr#' + 'x'*43
        self.pilot.reader.status = AsyncMock(return_value='Ожидает входа по QR')
        self.pilot.reader.account_id = AsyncMock(return_value=None)
        self.pilot.start = AsyncMock()
        result = await self.pilot.qr()
        self.assertIn('обновляемый QR', result)
        self.assertIn('/qr#', result)
        self.pilot.auth_portal.qr_link.assert_called_once_with()
        self.pilot.api.send_photo.assert_not_called()

    async def test_restart_resumes_only_consented_profile(self):
        self.pilot.store.consent()
        self.pilot.start = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.pilot.run()
        self.pilot.start.assert_awaited_once()

    async def test_manual_scan_reports_cooldown_without_rechecking(self):
        self.pilot.store.consent()
        self.pilot.store.select_source("moscowfilms")
        now=int(time.time())
        self.pilot.store.update_source("moscowfilms",error=None,delay=3600,now=now)
        self.pilot.reader.scan_once=AsyncMock()

        result=await self.pilot.scan()

        self.assertIn("Свежая проверка не запускалась",result)
        self.assertIn("интервал",result)
        self.pilot.reader.scan_once.assert_not_awaited()

    async def test_manual_scan_labels_only_due_sources_as_checked_now(self):
        self.pilot.store.consent()
        self.pilot.store.select_source("moscowfilms")
        async def scan_once():
            self.pilot.store.update_source("moscowfilms",error=None,visible=12,new=3)
        self.pilot.reader.scan_once=AsyncMock(side_effect=scan_once)
        self.pilot.deliver=AsyncMock()

        result=await self.pilot.scan()

        self.assertIn("проверено сейчас; видно 12, новых 3",result)
        self.pilot.reader.scan_once.assert_awaited_once()
