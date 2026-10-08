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
