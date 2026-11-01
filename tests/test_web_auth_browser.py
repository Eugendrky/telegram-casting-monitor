"""Opt-in Chromium tests; no real Telegram credentials or network required."""
import asyncio
import http.client
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from app.cloud_web_reader import CloudWebReader
from app.web_auth_portal import AuthPortal


@unittest.skipUnless(os.getenv('WEB_BROWSER_TESTS') == '1', 'Chromium integration suite')
class AuthBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        options = {'headless': True, 'args': ['--no-sandbox']}
        if os.getenv('TEST_CHROMIUM_PATH'):
            options['executable_path'] = os.environ['TEST_CHROMIUM_PATH']
        try:
            self.browser = await self.pw.chromium.launch(**options)
        except Exception:
            await self.pw.stop()
            raise
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_password_submit_and_redacted_rejection_on_official_origin(self):
        fixture = '''<input type="password"><script>
        document.querySelector('input').addEventListener('keypress', e => {
          if(e.key !== 'Enter') return;
          if(e.target.value === 'test-only') {
            window.appImManager = {managers: {appUsersManager: {
              getSelf: async () => ({id: 101})}}};
            const div = document.createElement('div');
            div.id = 'column-left'; document.body.append(div);
          } else { e.target.classList.add('error'); }
        });</script>'''
        await self.page.route('https://web.telegram.org/**',
            lambda route: route.fulfill(body=fixture, content_type='text/html'))
        await self.page.goto('https://web.telegram.org/k/')
        reader = CloudWebReader(None, None, profile_dir='/unused')
        reader._page = self.page
        await reader.submit_password('wrong-test-only')
        self.assertIsNone(await reader.account_id())
        self.assertEqual(await self.page.locator('input').input_value(), '')
        await reader.submit_password('test-only')
        self.assertEqual(await reader.account_id(), '101')
        self.assertEqual(await self.page.locator('input').input_value(), '')
        await self.page.route('https://attacker.test/**',
            lambda route: route.fulfill(body='<input type="password">'))
        await self.page.goto('https://attacker.test/')
        with self.assertRaises(ValueError):
            await reader.submit_password('test-only')
        # The foreign-origin fixture has no password field; rejection happens
        # before the value is written to any page.
        self.assertEqual(await self.page.locator('input').count(), 0)

    async def test_persistent_profile_survives_browser_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            for restart in (False, True):
                context = await self.pw.chromium.launch_persistent_context(
                    folder, headless=True, args=['--no-sandbox'])
                try:
                    page = await context.new_page()
                    await page.route('https://profile.test/**',
                        lambda route: route.fulfill(body='<title>Test</title>'))
                    await page.goto('https://profile.test/')
                    if restart:
                        self.assertEqual(await page.evaluate('localStorage.getItem("test")'), 'persistent')
                    else:
                        await page.evaluate('localStorage.setItem("test", "persistent")')
                finally:
                    await context.close()
    async def test_portal_javascript_csp_and_single_use_without_token_leak(self):
        pilot = Mock()
        pilot.password_ready = AsyncMock(return_value='Введите пароль')
        pilot.submit_password = AsyncMock(return_value='Вход завершён')
        portal = AuthPortal(pilot, 'https://bot.test', asyncio.get_running_loop(),
                            host='127.0.0.1', port=0)
        portal.start()
        errors = []
        self.page.on('pageerror', lambda exc: errors.append(str(exc)))

        async def proxy(route):
            request = route.request
            def forward():
                conn = http.client.HTTPConnection('127.0.0.1', portal.server.server_port)
                path = request.url.replace('https://bot.test', '')
                conn.request(request.method, path, request.post_data,
                    {k: v for k, v in request.headers.items() if k != 'host'})
                response = conn.getresponse()
                data = response.status, dict(response.getheaders()), response.read()
                conn.close()
                return data
            status, headers, body = await asyncio.to_thread(forward)
            await route.fulfill(status=status, headers=headers, body=body)

        try:
            await self.page.route('https://bot.test/**', proxy)
            token = portal.tickets.issue()
            await self.page.goto('https://bot.test/auth#'+token)
            await self.page.wait_for_function("!document.querySelector('button').disabled")
            self.assertEqual(self.page.url, 'https://bot.test/auth')
            await self.page.locator('input').fill('test-only')
            await self.page.locator('button').click()
            await self.page.get_by_role('status').get_by_text('Вход завершён').wait_for()
            self.assertEqual(await self.page.locator('input').input_value(), '')
            self.assertFalse(portal.tickets.valid(token))
            pilot.submit_password.assert_awaited_once_with('test-only')
            self.assertEqual(errors, [])
        finally:
            await asyncio.to_thread(portal.close)
