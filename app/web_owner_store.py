"""Isolated per-user casting inbox for Telegram Web imports.

No Telegram credentials or browser data are kept in this database.
Only approved source publications reach each user's delivery queue.
"""
from __future__ import annotations

import time
from app.casting_quality import actionable
from app.config import Profile
from app.matcher import match_post
from app.post_identity import fingerprint
from app.public_channels import normalize_channel

PILOT_SOURCES = frozenset(("castingiot4000r", "kinocekh", "moscowfilms"))


class WebOwnerStore:
    def __init__(self, db, owner_id: int):
        if not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError("Bot owner ID required")
        self.db = db
        self.owner_id = owner_id
        with db._lock, db.connection:
            db.connection.executescript("""
              CREATE TABLE IF NOT EXISTS web_owner_consent (
                user_id INTEGER PRIMARY KEY, consented_at INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS web_owner_sources (
                user_id INTEGER NOT NULL, username TEXT NOT NULL,
                last_id INTEGER NOT NULL DEFAULT 0,
                next_scan INTEGER NOT NULL DEFAULT 0,
                checked_at INTEGER NOT NULL DEFAULT 0,
                successful_at INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                visible_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id,username)
              );
              CREATE TABLE IF NOT EXISTS web_owner_posts (
                user_id INTEGER NOT NULL, username TEXT NOT NULL,
                message_id INTEGER NOT NULL, posted_at INTEGER NOT NULL,
                body TEXT NOT NULL, fingerprint TEXT NOT NULL,
                delivery TEXT NOT NULL DEFAULT 'archived',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry INTEGER NOT NULL DEFAULT 0,
                verdict TEXT,
                PRIMARY KEY(user_id,username,message_id)
              );
              CREATE INDEX IF NOT EXISTS idx_web_owner_delivery
                ON web_owner_posts(user_id,delivery,next_retry);
            """)

    def consented(self) -> bool:
        with self.db._lock:
            return self.db.connection.execute(
                "SELECT 1 FROM web_owner_consent WHERE user_id=?", (self.owner_id,)
            ).fetchone() is not None

    def consent(self):
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                """INSERT INTO web_owner_consent(user_id,consented_at)
                   VALUES(?,?) ON CONFLICT(user_id)
                   DO UPDATE SET consented_at=excluded.consented_at""",
                (self.owner_id, int(time.time())),
            )

    def select_source(self, raw: str) -> str:
        username = normalize_channel(raw)
        if not self.consented():
            raise ValueError("Сначала подтвердите подключение через /webconnect")
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                "INSERT OR IGNORE INTO web_owner_sources(user_id,username) VALUES(?,?)",
                (self.owner_id,username))
            # Joining a source and selecting it again is an explicit request to
            # retry a previous lookup miss. Clear only that recoverable error;
            # Telegram rate limits and other failures must keep their cooldown.
            self.db.connection.execute(
                """UPDATE web_owner_sources SET next_scan=0,last_error=NULL
                   WHERE user_id=? AND username=?
                     AND last_error IN (
                       'Источник не найден среди первых 500 чатов Telegram Web',
                       'Группа отсутствует среди доступных чатов аккаунта'
                     )""",
                (self.owner_id,username))
        return username

    def remove_source(self, raw: str) -> bool:
        username = normalize_channel(raw)
        with self.db._lock, self.db.connection:
            changed = self.db.connection.execute(
                "DELETE FROM web_owner_sources WHERE user_id=? AND username=?",
                (self.owner_id,username)).rowcount
        return bool(changed)

    def sources(self):
        with self.db._lock:
            return list(self.db.connection.execute(
                """SELECT username,last_id,next_scan,checked_at,successful_at,
                          last_error,visible_count,new_count,truncated
                   FROM web_owner_sources WHERE user_id=? ORDER BY username""",
                (self.owner_id,)))

    def due(self, now: int | None = None):
        now = int(time.time()) if now is None else int(now)
        return [row for row in self.sources() if row[2] <= now]

    def update_source(self, username: str, *, cursor: int | None = None,
                      error: str | None = None, delay: int = 1800,
                      visible: int = 0, new: int = 0, truncated: bool = False,
                      now: int | None = None):
        now = int(time.time()) if now is None else int(now)
        with self.db._lock, self.db.connection:
            self.db.connection.execute(
                """UPDATE web_owner_sources SET
                   last_id=CASE WHEN ? IS NULL THEN last_id ELSE MAX(last_id,?) END,
                   next_scan=?,checked_at=?,
                   successful_at=CASE WHEN ? IS NULL THEN ? ELSE successful_at END,
                   last_error=?,visible_count=?,new_count=?,truncated=?
                   WHERE user_id=? AND username=?""",
                (cursor,cursor,now+max(900,int(delay)),now,error,now,
                 error,visible,new,int(bool(truncated)),self.owner_id,username))

    def add_post(self, username: str, message_id: int, body: str, posted_at: int):
        username = normalize_channel(username)
        if username not in {row[0] for row in self.sources()}:
            raise ValueError("Источник не выбран")
        if not isinstance(message_id,int) or message_id <= 0:
            return False
        if not isinstance(body,str) or not body.strip() or posted_at <= 0:
            return False
        eligible,_ = actionable(body)
        if not eligible:
            return False
        data = self.db.profile(self.owner_id)
        text = body.strip()[:8000]
        digest = fingerprint(text)
        matched = False
        if data is not None and data["active"]:
            p = Profile(city=data["city"],gender=data["gender"],age=data["age"],
                        height_cm=data["height_cm"],kids_ok=False,
                        include_keywords=[],exclude_keywords=[
                            "набор закрыт","уже набрали","итоги кастинга"])
            matched = match_post(text,p).matched
        with self.db._lock,self.db.connection:
            if matched:
                cutoff=int(time.time())-14*86400
                already=self.db.connection.execute(
                    """SELECT 1 FROM web_owner_posts
                       WHERE user_id=? AND fingerprint=?
                         AND delivery IN ('pending','sending','sent')
                         AND posted_at>=? LIMIT 1""",
                    (self.owner_id,digest,cutoff)).fetchone()
                if already:
                    matched=False
            row=self.db.connection.execute(
                """INSERT OR IGNORE INTO web_owner_posts
                   (user_id,username,message_id,posted_at,body,fingerprint,delivery)
                   VALUES(?,?,?,?,?,?,?)""",
                (self.owner_id,username,message_id,int(posted_at),text,digest,
                 "pending" if matched else "archived"))
            return bool(row.rowcount)

    def claim_next(self, now: int | None = None):
        now=int(time.time()) if now is None else int(now)
        data=self.db.profile(self.owner_id)
        if not data or not data["active"]:
            return None
        with self.db._lock,self.db.connection:
            row=self.db.connection.execute(
                """SELECT username,message_id,body,posted_at
                   FROM web_owner_posts WHERE user_id=? AND delivery='pending'
                     AND attempts<4 AND next_retry<=?
                   ORDER BY posted_at,message_id LIMIT 1""",
                (self.owner_id,now)).fetchone()
            if row:
                self.db.connection.execute(
                    """UPDATE web_owner_posts SET delivery='sending'
                       WHERE user_id=? AND username=? AND message_id=?""",
                    (self.owner_id,row[0],row[1]))
        return row

    def mark_delivery(self, username: str, message_id: int, status: str):
        if status not in ("sent","retry","blocked"):
            raise ValueError("Invalid delivery outcome")
        with self.db._lock,self.db.connection:
            row=self.db.connection.execute(
                """SELECT attempts FROM web_owner_posts WHERE user_id=?
                   AND username=? AND message_id=? AND delivery='sending'""",
                (self.owner_id,username,message_id)).fetchone()
            if row is None:
                return False
            attempts=row[0]+1
            result=("sent" if status=="sent" else
                    "blocked" if status=="blocked" else
                    "failed" if attempts>=4 else "pending")
            retry=(int(time.time())+min(3600,60*(2**(attempts-1)))
                   if result=="pending" else 0)
            self.db.connection.execute(
                """UPDATE web_owner_posts SET delivery=?,attempts=?,next_retry=?
                   WHERE user_id=? AND username=? AND message_id=?""",
                (result,attempts,retry,self.owner_id,username,message_id))
        return True

    def add_feedback(self, username: str, message_id: int, verdict: str) -> bool:
        if verdict not in ("relevant","irrelevant"):
            return False
        with self.db._lock,self.db.connection:
            return bool(self.db.connection.execute(
                """UPDATE web_owner_posts SET verdict=?
                   WHERE user_id=? AND username=? AND message_id=? AND delivery='sent'""",
                (verdict,self.owner_id,username,message_id)).rowcount)

    def latest(self, limit: int = 8):
        with self.db._lock:
            return list(self.db.connection.execute(
                """SELECT username,message_id,body,posted_at
                   FROM web_owner_posts WHERE user_id=? AND delivery='sent'
                   ORDER BY posted_at DESC LIMIT ?""",
                (self.owner_id,max(1,min(int(limit),20)))))

    def stats(self):
        with self.db._lock:
            return dict(self.db.connection.execute(
                """SELECT delivery,COUNT(*) FROM web_owner_posts
                   WHERE user_id=? GROUP BY delivery""",(self.owner_id,)))

    def forget(self):
        with self.db._lock,self.db.connection:
            for table in ("web_owner_posts","web_owner_sources","web_owner_consent"):
                self.db.connection.execute("DELETE FROM "+table+" WHERE user_id=?",
                                           (self.owner_id,))
