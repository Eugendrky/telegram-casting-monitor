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
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(selected_sources)")}
        for column, kind in (("peer_id", "INTEGER"), ("access_hash", "INTEGER"),
                             ("peer_kind", "TEXT"), ("username", "TEXT")):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE selected_sources ADD COLUMN {column} {kind}")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS source_cursors (
                chat_id INTEGER PRIMARY KEY, last_message_id INTEGER NOT NULL DEFAULT 0
            )"""
        )
        custom_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(custom_sources)")}
        for column, kind in (("peer_id", "INTEGER"), ("access_hash", "INTEGER"), ("peer_kind", "TEXT")):
            if column not in custom_columns:
                self._conn.execute(f"ALTER TABLE custom_sources ADD COLUMN {column} {kind}")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS castings (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                source_title TEXT NOT NULL,
                text TEXT NOT NULL,
                message_link TEXT NOT NULL,
                score INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                delivered INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, message_id)
            )
        """)
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
        return "selected"

    def set_source_mode(self, mode: str) -> None:
        if mode != "selected":
            raise ValueError("invalid source mode")
        self._conn.execute(
            "INSERT INTO preferences(name, value) VALUES ('source_mode', ?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (mode,),
        )
        self._conn.commit()

    def selected_source_ids(self) -> set[int]:
        return {row[0] for row in self._conn.execute("SELECT chat_id FROM selected_sources")}

    def selected_sources(self) -> list[tuple[int, str, int | None, int | None, str | None, str | None]]:
        return list(self._conn.execute(
            "SELECT chat_id, title, peer_id, access_hash, peer_kind, username FROM selected_sources"
            " ORDER BY title"
        ))

    def cursor(self, chat_id: int) -> int:
        row = self._conn.execute(
            "SELECT last_message_id FROM source_cursors WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def advance_cursor(self, chat_id: int, message_id: int) -> None:
        self._conn.execute(
            """INSERT INTO source_cursors(chat_id, last_message_id) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET last_message_id =
            MAX(source_cursors.last_message_id, excluded.last_message_id)""",
            (chat_id, message_id),
        )
        self._conn.commit()

    def update_source_peer(self, chat_id: int, peer_id: int, access_hash: int | None,
                           peer_kind: str, username: str | None) -> None:
        self._conn.execute(
            """UPDATE selected_sources SET peer_id=?, access_hash=?, peer_kind=?, username=?
               WHERE chat_id=?""",
            (peer_id, access_hash, peer_kind, username, chat_id),
        )
        self._conn.commit()

    def toggle_source(self, chat_id: int, title: str, *, peer_id: int | None = None,
                      access_hash: int | None = None, peer_kind: str | None = None,
                      username: str | None = None) -> bool:
        if self._conn.execute("SELECT 1 FROM selected_sources WHERE chat_id = ?", (chat_id,)).fetchone():
            self._conn.execute("DELETE FROM selected_sources WHERE chat_id = ?", (chat_id,))
            selected = False
        else:
            self._conn.execute(
                """INSERT INTO selected_sources
                   (chat_id, title, peer_id, access_hash, peer_kind, username)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chat_id, title, peer_id, access_hash, peer_kind, username),
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

    def monitored_sources(self) -> list[dict]:
        sources = [{
            "kind": "selected", "chat_id": row[0], "title": row[1],
            "peer_id": row[2], "access_hash": row[3],
            "peer_kind": row[4], "username": row[5],
        } for row in self.selected_sources()]
        sources += [{
            "kind": "custom", "chat_id": row[2], "title": row[1] or row[0],
            "peer_id": row[3], "access_hash": row[4],
            "peer_kind": row[5], "username": row[0],
        } for row in self._conn.execute(
            "SELECT username, title, chat_id, peer_id, access_hash, peer_kind FROM custom_sources ORDER BY username"
        )]
        return sources

    def update_custom_peer(self, username: str, *, chat_id: int, title: str,
                           peer_id: int, access_hash: int | None, peer_kind: str) -> None:
        self._conn.execute(
            """UPDATE custom_sources
               SET chat_id=?, title=?, peer_id=?, access_hash=?, peer_kind=?
               WHERE username=?""",
            (chat_id, title, peer_id, access_hash, peer_kind, username),
        )
        self._conn.commit()

    def remove_selected_source(self, chat_id: int) -> None:
        self._conn.execute("DELETE FROM selected_sources WHERE chat_id=?", (chat_id,))
        self._conn.commit()

    def record_casting(self, chat_id: int, message_id: int, title: str,
                       text: str, link: str, score: int, reason: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO castings
               (chat_id, message_id, source_title, text, message_link, score, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, message_id, title, text[:4500], link, score, reason[:500]),
        )
        self._conn.commit()

    def mark_delivered(self, chat_id: int, message_id: int) -> None:
        self._conn.execute(
            "UPDATE castings SET delivered=1 WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        )
        self._conn.commit()

    def latest_castings(self, limit: int = 10) -> list[tuple]:
        return list(self._conn.execute(
            """SELECT source_title, text, message_link, score, reason, delivered
               FROM castings ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (max(1, min(limit, 30)),),
        ))

    def close(self) -> None:
        self._conn.close()
