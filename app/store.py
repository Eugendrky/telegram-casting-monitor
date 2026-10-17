from __future__ import annotations

import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                matched INTEGER NOT NULL DEFAULT 0,
                seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS selected_sources (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_sources (
                username TEXT PRIMARY KEY,
                title TEXT,
                chat_id INTEGER
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_seen(self, chat_id: int, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return row is not None

    def mark(self, chat_id: int, message_id: int, matched: bool) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO seen (chat_id, message_id, matched)
            VALUES (?, ?, ?)
            """,
            (chat_id, message_id, int(matched)),
        )
        self._conn.commit()

    def source_mode(self) -> str:
        row = self._conn.execute(
            "SELECT value FROM preferences WHERE name = 'source_mode'"
        ).fetchone()
        return row[0] if row is not None else "configured"

    def set_source_mode(self, mode: str) -> None:
        if mode not in {"all", "selected", "configured"}:
            raise ValueError("invalid source mode")
        self._conn.execute(
            "INSERT INTO preferences(name, value) VALUES ('source_mode', ?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (mode,),
        )
        self._conn.commit()

    def selected_source_ids(self) -> set[int]:
        return {row[0] for row in self._conn.execute("SELECT chat_id FROM selected_sources")}

    def toggle_source(self, chat_id: int, title: str) -> bool:
        if self._conn.execute("SELECT 1 FROM selected_sources WHERE chat_id = ?", (chat_id,)).fetchone():
            self._conn.execute("DELETE FROM selected_sources WHERE chat_id = ?", (chat_id,))
            selected = False
        else:
            self._conn.execute(
                "INSERT INTO selected_sources(chat_id, title) VALUES (?, ?)",
                (chat_id, title),
            )
            selected = True
        self._conn.execute(
            "INSERT INTO preferences(name, value) VALUES ('source_mode', 'selected') "
            "ON CONFLICT(name) DO UPDATE SET value='selected'"
        )
        self._conn.commit()
        return selected

    def add_custom_source(self, username: str, title: str | None = None, chat_id: int | None = None) -> None:
        username = username.lstrip("@").lower()
        self._conn.execute(
            "INSERT OR REPLACE INTO custom_sources(username, title, chat_id) VALUES (?, ?, ?)",
            (username, title or username, chat_id),
        )
        self._conn.execute(
            "INSERT INTO preferences(name, value) VALUES ('source_mode', 'selected') "
            "ON CONFLICT(name) DO UPDATE SET value='selected'"
        )
        self._conn.commit()

    def remove_custom_source(self, username: str) -> None:
        username = username.lstrip("@").lower()
        self._conn.execute("DELETE FROM custom_sources WHERE username = ?", (username,))
        self._conn.commit()

    def custom_sources(self) -> list[tuple[str, str | None, int | None]]:
        return list(self._conn.execute("SELECT username, title, chat_id FROM custom_sources"))

    def close(self) -> None:
        self._conn.close()
