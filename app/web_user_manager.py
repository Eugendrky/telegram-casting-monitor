"""Per-user, consent-gated Telegram Web sessions for the casting bot."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from app.owner_web_pilot import OwnerWebPilot

LOG = logging.getLogger("casting.webusers")


class WebUserManager:
    """Own one isolated browser profile and delivery store per Telegram user."""

    def __init__(self, db, api, owner_id: int, *, root="/data", max_users: int | None=None):
        self.db, self.api, self.owner_id = db, api, owner_id
        self.root = Path(root)
        try:
            configured = int(os.getenv("WEB_QR_MAX_USERS", "5"))
        except ValueError:
            configured = 5
        self.max_users = max(1, min(20, configured if max_users is None else int(max_users)))
        self.pilots: dict[int, OwnerWebPilot] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.active: set[int] = set()
        self.auth_portal = None
        self._lock = asyncio.Lock()
        with db._lock, db.connection:
            db.connection.execute("""CREATE TABLE IF NOT EXISTS web_user_allowlist (
                username TEXT PRIMARY KEY,
                user_id INTEGER UNIQUE,
                added_at INTEGER NOT NULL DEFAULT (unixepoch())
            )""")

    @staticmethod
    def normalize_username(raw: str) -> str:
        value = (raw or "").strip().removeprefix("@").casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", value):
            raise ValueError("Укажите Telegram username, например @actor_name.")
        return value

    def allow(self, raw: str) -> str:
        username = self.normalize_username(raw)
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                "INSERT OR IGNORE INTO web_user_allowlist(username) VALUES(?)", (username,)
            )
        return username

    def deny(self, raw: str) -> tuple[str, int | None, bool]:
        username = self.normalize_username(raw)
        with self.db._lock, self.db.connection:
            row = self.db.connection.execute(
                "SELECT user_id FROM web_user_allowlist WHERE username=?", (username,)
            ).fetchone()
            if row is None:
                return username, None, False
            self.db.connection.execute(
                "DELETE FROM web_user_allowlist WHERE username=?", (username,)
            )
        return username, int(row[0]) if row[0] is not None else None, True

    def allowed_users(self):
        with self.db._lock:
            return list(self.db.connection.execute(
                "SELECT username,user_id,added_at FROM web_user_allowlist ORDER BY username"
            ))

    def is_allowed(self, user_id: int, telegram_username: str | None) -> bool:
        if user_id == self.owner_id:
            return True
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return False
        try:
            username = self.normalize_username(telegram_username or "")
        except ValueError:
            username = None
        with self.db._lock, self.db.connection:
            bound = self.db.connection.execute(
                "SELECT username FROM web_user_allowlist WHERE user_id=?", (user_id,)
            ).fetchone()
            if bound:
                return True
            if username is None:
                return False
            row = self.db.connection.execute(
                "SELECT user_id FROM web_user_allowlist WHERE username=?", (username,)
            ).fetchone()
            if row is None:
                return False
            if row[0] is not None:
                return int(row[0]) == user_id
            try:
                self.db.connection.execute(
                    "UPDATE web_user_allowlist SET user_id=? WHERE username=? AND user_id IS NULL",
                    (user_id, username),
                )
            except Exception:
                return False
            return True

    def is_id_allowed(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return True
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return False
        with self.db._lock:
            return self.db.connection.execute(
                "SELECT 1 FROM web_user_allowlist WHERE user_id=?", (user_id,)
            ).fetchone() is not None

    def profile_dir(self, user_id: int) -> Path:
        if user_id == self.owner_id:
            # Preserve the existing owner's authenticated session on upgrade.
            return self.root / "telegram-web-owner"
        return self.root / "telegram-web-users" / str(user_id)

    def get(self, user_id: int, *, create: bool=True):
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("Нужен личный Telegram-аккаунт.")
        pilot = self.pilots.get(user_id)
        if pilot is None and create:
            pilot = OwnerWebPilot(self.db, self.api, user_id,
                                  profile_dir=str(self.profile_dir(user_id)))
            pilot.auth_portal = (self.auth_portal.for_user(user_id)
                                 if self.auth_portal is not None else None)
            self.pilots[user_id] = pilot
        return pilot

    def consented_users(self) -> list[int]:
        with self.db._lock:
            try:
                rows = self.db.connection.execute(
                    """SELECT c.user_id FROM web_owner_consent c
                       WHERE c.user_id=? OR EXISTS (
                         SELECT 1 FROM web_user_allowlist a WHERE a.user_id=c.user_id
                       ) ORDER BY c.consented_at,c.user_id""", (self.owner_id,)
                ).fetchall()
            except Exception:
                return []
        return [int(row[0]) for row in rows if int(row[0]) > 0]

    def _reserve(self, user_id: int):
        if user_id in self.active:
            return
        if len(self.active) >= self.max_users:
            raise ValueError(
                f"Сейчас подключено максимальное число аккаунтов ({self.max_users}). "
                "Попробуйте позже или отключите ненужный аккаунт командой /webdisconnect."
            )
        self.active.add(user_id)

    async def activate(self, user_id: int):
        async with self._lock:
            pilot = self.get(user_id)
            if not pilot.store.consented():
                raise ValueError("Сначала подтвердите подключение через /webconnect.")
            self._reserve(user_id)
        try:
            await pilot.start()
        except Exception:
            self.active.discard(user_id)
            raise
        task = self.tasks.get(user_id)
        if task is None or task.done():
            self.tasks[user_id] = asyncio.create_task(pilot.run())
        return pilot

    async def consent(self, user_id: int):
        pilot = self.get(user_id)
        if not pilot.store.consented():
            async with self._lock:
                self._reserve(user_id)
                pilot.store.consent()
        return await self.activate(user_id)

    async def restore(self):
        ids = self.consented_users()
        if self.owner_id in ids:
            ids.remove(self.owner_id)
            ids.insert(0, self.owner_id)
        for user_id in ids[:self.max_users]:
            try:
                await self.activate(user_id)
            except Exception as exc:
                LOG.warning("Telegram Web session for user %s could not resume: %s",
                            user_id, type(exc).__name__)
        if len(ids) > self.max_users:
            LOG.error("%s consented Telegram Web profiles exceed configured limit %s",
                      len(ids), self.max_users)

    async def disconnect(self, user_id: int):
        pilot = self.get(user_id, create=True)
        if (not pilot.store.consented() and not pilot.reader.profile_dir.exists()
                and not pilot.store.sources()):
            return "Подключённой сессии нет."
        try:
            await pilot.disconnect()
        finally:
            self.active.discard(user_id)
        return ("Сессия и данные сборщика удалены. Для полного выхода также завершите "
                "сеанс в Telegram → Настройки → Устройства.")

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for pilot in self.pilots.values():
            if pilot.reader._context:
                await pilot.reader._context.close()
                pilot.reader._context = None
            if pilot.reader._pw:
                await pilot.reader._pw.stop()
                pilot.reader._pw = None
            pilot.reader._page = None
            pilot.started = False
        self.active.clear()
