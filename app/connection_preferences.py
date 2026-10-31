"""Isolated, optional source preferences for a future per-user connection.

This module stores only a Telegram user ID, an acknowledgement that the
feature is experimental, and explicitly chosen PUBLIC source usernames.
It never authenticates to Telegram or stores session credentials.
"""
from __future__ import annotations
import time

from app.public_channels import normalize_channel

MAX_SOURCES = 20


class ConnectionPreferences:
    def __init__(self, db):
        self.db = db
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                """CREATE TABLE IF NOT EXISTS connection_preferences (
                    user_id INTEGER PRIMARY KEY,
                    acknowledged_at INTEGER NOT NULL
                )"""
            )
            self.db.connection.execute(
                """CREATE TABLE IF NOT EXISTS user_selected_sources (
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    PRIMARY KEY(user_id, username)
                )"""
            )

    def grant(self, user_id: int):
        """Acknowledge that source selection is experimental, NOT account login."""
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("invalid user")
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                """INSERT INTO connection_preferences(user_id,acknowledged_at)
                   VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET
                   acknowledged_at=excluded.acknowledged_at""",
                (user_id, int(time.time())),
            )

    def consented(self, user_id: int) -> bool:
        with self.db._lock:
            return self.db.connection.execute(
                "SELECT 1 FROM connection_preferences WHERE user_id=?",
                (user_id,),
            ).fetchone() is not None

    def select_source(self, user_id: int, source: str) -> str:
        name = normalize_channel(source)
        with self.db._lock, self.db.connection:
            if not self.consented(user_id):
                raise ValueError("Сначала подтвердите ознакомление командой /connect")
            existing = self.db.connection.execute(
                "SELECT 1 FROM user_selected_sources WHERE user_id=? AND username=?",
                (user_id, name),
            ).fetchone()
            if existing:
                return name
            count = self.db.connection.execute(
                "SELECT COUNT(*) FROM user_selected_sources WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]
            if count >= MAX_SOURCES:
                raise ValueError("Можно выбрать не больше 20 источников")
            self.db.connection.execute(
                "INSERT INTO user_selected_sources(user_id,username) VALUES (?,?)",
                (user_id, name),
            )
        return name

    def sources(self, user_id: int) -> list[str]:
        with self.db._lock:
            return [row[0] for row in self.db.connection.execute(
                "SELECT username FROM user_selected_sources WHERE user_id=? ORDER BY username",
                (user_id,),
            )]

    def remove_source(self, user_id: int, source: str) -> bool:
        name = normalize_channel(source)
        with self.db._lock, self.db.connection:
            cursor = self.db.connection.execute(
                "DELETE FROM user_selected_sources WHERE user_id=? AND username=?",
                (user_id, name),
            )
        return bool(cursor.rowcount)

    def clear(self, user_id: int):
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                "DELETE FROM user_selected_sources WHERE user_id=?",
                (user_id,),
            )
            self.db.connection.execute(
                "DELETE FROM connection_preferences WHERE user_id=?",
                (user_id,),
            )
