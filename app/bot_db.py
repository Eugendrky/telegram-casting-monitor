"""Persistent state for a Bot API-only casting monitor."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
 user_id INTEGER PRIMARY KEY,
 city TEXT NOT NULL DEFAULT '',
 gender TEXT NOT NULL DEFAULT 'any',
 age INTEGER,
 height_cm INTEGER,
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources (
 chat_id INTEGER PRIMARY KEY,
 title TEXT NOT NULL,
 username TEXT,
 kind TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0,
 joined INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_preferences (
 user_id INTEGER NOT NULL,
 chat_id INTEGER NOT NULL,
 following INTEGER NOT NULL,
 PRIMARY KEY(user_id,chat_id)
);
CREATE TABLE IF NOT EXISTS castings (
 chat_id INTEGER NOT NULL,
 message_id INTEGER NOT NULL,
 title TEXT NOT NULL,
 body TEXT NOT NULL,
 url TEXT,
 posted_at INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS deliveries (
 user_id INTEGER NOT NULL,
 chat_id INTEGER NOT NULL,
 message_id INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 PRIMARY KEY(user_id,chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS meta (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries(status);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    def cursor(self) -> int:
        row = self.connection.execute("SELECT value FROM meta WHERE key='update_id'").fetchone()
        return int(row[0]) if row else 0

    def advance(self, update_id: int):
        with self.connection:
            self.connection.execute(
                "INSERT INTO meta(key,value) VALUES('update_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER))",
                (str(update_id),)
            )

    def register(self, user_id: int):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO subscribers(user_id) VALUES (?)", (user_id,)
            )

    def profile(self, user_id: int):
        row = self.connection.execute(
            "SELECT city,gender,age,height_cm,active FROM subscribers WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("city","gender","age","height_cm","active"), row))

    def update_profile(self, user_id: int, key: str, value):
        if key not in {"city","gender","age","height_cm","active"}:
            raise ValueError("invalid profile field")
        self.register(user_id)
        with self.connection:
            self.connection.execute(f"UPDATE subscribers SET {key}=? WHERE user_id=?",
                                    (value,user_id))

    def subscribers(self, chat_id: int):
        return self.connection.execute(
            """SELECT u.user_id,u.city,u.gender,u.age,u.height_cm FROM subscribers u
               LEFT JOIN source_preferences p ON p.user_id=u.user_id AND p.chat_id=?
               WHERE u.active=1 AND COALESCE(p.following,1)=1 ORDER BY u.user_id""",
            (chat_id,)
        ).fetchall()

    def set_source(self, chat_id: int, title: str, username: str | None,
                   kind: str, joined: bool):
        if kind not in {"channel","supergroup","group"}:
            return
        with self.connection:
            self.connection.execute(
                """INSERT INTO sources(chat_id,title,username,kind,joined)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,
                   username=excluded.username, kind=excluded.kind,
                   joined=excluded.joined,updated_at=CURRENT_TIMESTAMP""",
                (chat_id,title,username,kind,int(joined))
            )
            if not joined:
                self.connection.execute("UPDATE sources SET enabled=0 WHERE chat_id=?", (chat_id,))

    def source(self, chat_id: int):
        return self.connection.execute(
            "SELECT chat_id,title,kind,enabled,joined,username FROM sources WHERE chat_id=?",
            (chat_id,)
        ).fetchone()

    def sources(self):
        return self.connection.execute(
            "SELECT chat_id,title,kind,enabled,joined,username FROM sources ORDER BY title,chat_id"
        ).fetchall()

    def enable_source(self, chat_id: int, enabled: bool) -> bool:
        with self.connection:
            updated = self.connection.execute(
                "UPDATE sources SET enabled=? WHERE chat_id=? AND joined=1",
                (int(enabled), chat_id)
            ).rowcount
        return bool(updated)

    def following(self, user_id: int, chat_id: int):
        row = self.connection.execute(
            "SELECT following FROM source_preferences WHERE user_id=? AND chat_id=?",
            (user_id,chat_id)
        ).fetchone()
        return True if row is None else bool(row[0])

    def follow(self, user_id: int, chat_id: int, enabled: bool):
        with self.connection:
            self.connection.execute(
                """INSERT INTO source_preferences(user_id,chat_id,following)
                   VALUES (?,?,?) ON CONFLICT(user_id,chat_id)
                   DO UPDATE SET following=excluded.following""",
                (user_id,chat_id,int(enabled))
            )

    def record_post(self, chat_id: int, message_id: int, title: str,
                    body: str, url: str | None, posted_at: int | None):
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO castings
                   (chat_id,message_id,title,body,url,posted_at) VALUES (?,?,?,?,?,?)""",
                (chat_id,message_id,title,body[:8000],url,posted_at)
            )

    def queue(self, user_id: int, chat_id: int, message_id: int):
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO deliveries(user_id,chat_id,message_id)
                   VALUES(?,?,?)""",(user_id,chat_id,message_id)
            )

    def pending(self, limit: int = 12):
        return self.connection.execute(
            """SELECT d.user_id,d.chat_id,d.message_id,c.title,c.body,c.url
               FROM deliveries d JOIN castings c
                 ON c.chat_id=d.chat_id AND c.message_id=d.message_id
               JOIN subscribers s ON s.user_id=d.user_id
               WHERE d.status='pending' AND s.active=1
               ORDER BY c.created_at,d.rowid LIMIT ?""",(limit,)
        ).fetchall()

    def mark_delivery(self, user_id: int, chat_id: int, message_id: int,
                      status: str, error: str | None = None):
        if status not in {"sent","pending","blocked","failed"}:
            raise ValueError("invalid delivery status")
        with self.connection:
            self.connection.execute(
                """UPDATE deliveries SET status=?,attempts=attempts+1,last_error=?
                   WHERE user_id=? AND chat_id=? AND message_id=?""",
                (status,error,user_id,chat_id,message_id)
            )

    def latest(self, user_id: int, limit: int = 8):
        return self.connection.execute(
            """SELECT c.title,c.body,c.url FROM castings c
               JOIN deliveries d ON c.chat_id=d.chat_id AND c.message_id=d.message_id
               WHERE d.user_id=? AND d.status='sent'
               ORDER BY c.created_at DESC,c.message_id DESC LIMIT ?""",
            (user_id,max(1,min(limit,20)))
        ).fetchall()
