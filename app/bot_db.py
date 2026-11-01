"""Persistent state for a Bot API-only casting monitor."""
from __future__ import annotations

import sqlite3
import time
import threading
from functools import wraps
from pathlib import Path
from app.post_identity import fingerprint

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
CREATE TABLE IF NOT EXISTS public_state (
 chat_id INTEGER PRIMARY KEY,
 cursor INTEGER NOT NULL DEFAULT 0,
 next_scan INTEGER NOT NULL DEFAULT 0,
 last_error TEXT
);
CREATE TABLE IF NOT EXISTS suggestions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL,
 username TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at TEXT,
 UNIQUE(user_id, username)
);
CREATE INDEX IF NOT EXISTS idx_suggestions_queue ON suggestions(status,id);
CREATE TABLE IF NOT EXISTS casting_feedback (
 user_id INTEGER NOT NULL,
 chat_id INTEGER NOT NULL,
 message_id INTEGER NOT NULL,
 verdict TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(user_id,chat_id,message_id),
 FOREIGN KEY(user_id,chat_id,message_id) REFERENCES deliveries(user_id,chat_id,message_id)
);
CREATE TABLE IF NOT EXISTS suggestion_input (
 user_id INTEGER PRIMARY KEY,
 awaiting INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries(status);
"""

def synchronized(cls):
    """Keep one SQLite connection safe across Bot API and public scanner threads."""
    for name, method in tuple(vars(cls).items()):
        if name.startswith("_") or not callable(method):
            continue
        @wraps(method)
        def wrapped(self, *args, _method=method, **kwargs):
            with self._lock:
                return _method(self, *args, **kwargs)
        setattr(cls, name, wrapped)
    return cls


@synchronized
class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self._migrate("public_state", {
            "checked_at":"INTEGER NOT NULL DEFAULT 0",
            "last_success_at":"INTEGER NOT NULL DEFAULT 0",
            "last_posts":"INTEGER NOT NULL DEFAULT 0",
            "last_new":"INTEGER NOT NULL DEFAULT 0",
            "last_matched":"INTEGER NOT NULL DEFAULT 0",
            "client_cursor":"INTEGER NOT NULL DEFAULT 0",
            "client_next_scan":"INTEGER NOT NULL DEFAULT 0",
            "client_checked_at":"INTEGER NOT NULL DEFAULT 0",
        })
        self._migrate("deliveries", {"next_retry":"INTEGER NOT NULL DEFAULT 0"})
        self._migrate("castings", {"fingerprint":"TEXT"})
        existing=self.connection.execute(
            "SELECT chat_id,message_id,body FROM castings WHERE fingerprint IS NULL"
        ).fetchall()
        if existing:
            with self.connection:
                self.connection.executemany(
                    "UPDATE castings SET fingerprint=? WHERE chat_id=? AND message_id=?",
                    [(fingerprint(body),chat_id,message_id) for chat_id,message_id,body in existing]
                )

    def _migrate(self, table: str, fields: dict):
        existing={row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        with self.connection:
            for column, sql_type in fields.items():
                if column not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def add_feedback(self, user_id: int, chat_id: int, message_id: int, verdict: str) -> bool:
        if verdict not in ("relevant","irrelevant"):
            raise ValueError("invalid feedback")
        with self.connection:
            existing=self.connection.execute(
                "SELECT 1 FROM deliveries WHERE user_id=? AND chat_id=? AND message_id=?",
                (user_id,chat_id,message_id)
            ).fetchone()
            if not existing:
                return False
            self.connection.execute(
                """INSERT INTO casting_feedback(user_id,chat_id,message_id,verdict)
                   VALUES (?,?,?,?) ON CONFLICT(user_id,chat_id,message_id)
                   DO UPDATE SET verdict=excluded.verdict,created_at=CURRENT_TIMESTAMP""",
                (user_id,chat_id,message_id,verdict)
            )
        return True

    def feedback_stats(self):
        return dict(self.connection.execute(
            "SELECT verdict, COUNT(*) FROM casting_feedback GROUP BY verdict"
        ).fetchall())

    def set_profile_prompt(self, user_id: int, field: str | None):
        with self.connection:
            key="profile_prompt:{}".format(user_id)
            if field:
                self.connection.execute(
                    "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key,field))
            else:
                self.connection.execute("DELETE FROM meta WHERE key=?",(key,))

    def profile_prompt(self, user_id: int) -> str | None:
        row=self.connection.execute("SELECT value FROM meta WHERE key=?",
                                    ("profile_prompt:{}".format(user_id),)).fetchone()
        return row[0] if row else None

    def set_waiting_for_suggestion(self, user_id: int, enabled: bool):
        with self.connection:
            if enabled:
                self.connection.execute(
                    "INSERT INTO suggestion_input(user_id,awaiting) VALUES (?,1) "
                    "ON CONFLICT(user_id) DO UPDATE SET awaiting=1",
                    (user_id,)
                )
            else:
                self.connection.execute("DELETE FROM suggestion_input WHERE user_id=?", (user_id,))

    def awaiting_suggestion(self, user_id: int) -> bool:
        return bool(self.connection.execute(
            "SELECT 1 FROM suggestion_input WHERE user_id=? AND awaiting=1", (user_id,)
        ).fetchone())

    def propose(self, user_id: int, username: str) -> tuple[int, str]:
        from app.public_channels import normalize_channel
        username = normalize_channel(username)
        with self.connection:
            existing = self.connection.execute(
                "SELECT id,status FROM suggestions WHERE user_id=? AND username=?",
                (user_id, username),
            ).fetchone()
            if existing:
                return int(existing[0]), str(existing[1])
            cursor = self.connection.execute(
                "INSERT INTO suggestions(user_id,username) VALUES (?,?)",
                (user_id,username),
            )
            return int(cursor.lastrowid), "pending"

    def pending_suggestions(self, limit: int = 8, offset: int = 0):
        return list(self.connection.execute(
            "SELECT id,user_id,username,created_at FROM suggestions "
            "WHERE status='pending' ORDER BY id LIMIT ? OFFSET ?",
            (max(1,min(limit,30)),max(0,offset))
        ))

    def suggestion(self, suggestion_id: int):
        return self.connection.execute(
            "SELECT id,user_id,username,status FROM suggestions WHERE id=?",
            (suggestion_id,)
        ).fetchone()

    def review_suggestion(self, suggestion_id: int, approve: bool):
        """Atomic idempotent review; only approved suggestions enter the crawler."""
        with self.connection:
            row=self.connection.execute(
                "SELECT id,user_id,username,status FROM suggestions WHERE id=?",
                (suggestion_id,)
            ).fetchone()
            if not row or row[3] != "pending":
                return None
            next_status="approved" if approve else "rejected"
            self.connection.execute(
                "UPDATE suggestions SET status=?,reviewed_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='pending'",
                (next_status,suggestion_id)
            )
            channel_id=self.add_public(row[2]) if approve else None
            return (row[1],row[2],next_status,channel_id)

    def delete_user_data(self, user_id: int):
        """Remove this user's profile, delivery/feedback and subscription data.

        Shared castings and other subscribers must stay untouched.
        """
        if not isinstance(user_id, int) or user_id<=0:
            raise ValueError("invalid user")
        with self.connection:
            self.connection.execute("DELETE FROM casting_feedback WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM deliveries WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM source_preferences WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM suggestion_input WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM suggestions WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM subscribers WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM meta WHERE key=?", ("profile_prompt:"+str(user_id),))

    def admin_counts(self) -> dict:
        tables={"users":"subscribers","sources":"sources","posts":"castings"}
        result={key:self.connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0] for key,table in tables.items()}
        result["pending"]=self.connection.execute(
            "SELECT COUNT(*) FROM suggestions WHERE status='pending'"
        ).fetchone()[0]
        result["errors"]=self.connection.execute(
            "SELECT COUNT(*) FROM public_state WHERE last_error IS NOT NULL"
        ).fetchone()[0]
        return result

    def add_public(self, username: str):
        from app.public_channels import normalize_channel, source_id
        username = normalize_channel(username)
        chat_id = source_id(username)
        with self.connection:
            self.connection.execute(
                """INSERT INTO sources(chat_id,title,username,kind,enabled,joined)
                   VALUES(?,?,?,'public',1,1)
                   ON CONFLICT(chat_id) DO UPDATE SET enabled=1,joined=1""",
                (chat_id,"@"+username,username))
            self.connection.execute("INSERT OR IGNORE INTO public_state(chat_id) VALUES(?)",(chat_id,))
        return chat_id

    def public_sources(self):
        return list(self.connection.execute(
            "SELECT chat_id,title,kind,enabled,joined,username FROM sources WHERE kind='public' ORDER BY title"
        ))

    def client_cursor(self, chat_id: int) -> int:
        row=self.connection.execute(
            "SELECT client_cursor FROM public_state WHERE chat_id=?",(chat_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def advance_client_cursor(self, chat_id: int, message_id: int):
        with self.connection:
            self.connection.execute(
                "UPDATE public_state SET client_cursor=MAX(client_cursor,?) WHERE chat_id=?",
                (message_id,chat_id)
            )

    def should_scan_client(self, chat_id: int, *, now: int | None=None):
        now=int(time.time()) if now is None else int(now)
        row=self.connection.execute(
            "SELECT client_next_scan FROM public_state WHERE chat_id=?",(chat_id,)
        ).fetchone()
        return bool(row and row[0]<=now)

    def update_client_status(self, chat_id: int, *, error: str | None,
                             retry_after: int=1800, now: int | None=None):
        now=int(time.time()) if now is None else int(now)
        delay=max(900,int(retry_after))
        with self.connection:
            self.connection.execute(
                """UPDATE public_state
                   SET client_next_scan=?,client_checked_at=?,next_scan=?,
                       checked_at=?,last_error=?
                   WHERE chat_id=?""",
                (now+delay,now,now+delay,now,error,chat_id)
            )

    def client_scan_completed(self, chat_id: int, *, scanned: int,new_posts: int,
                              matched: int,interval: int=1800,
                              now: int | None=None):
        now=int(time.time()) if now is None else int(now)
        with self.connection:
            self.connection.execute(
                """UPDATE public_state SET client_next_scan=?,client_checked_at=?,
                   next_scan=?,checked_at=?,last_success_at=?,last_error=NULL,
                   last_posts=?,last_new=?,last_matched=?
                   WHERE chat_id=?""",
                (now+max(900,int(interval)),now,now+max(900,int(interval)),now,
                 now,scanned,new_posts,matched,chat_id)
            )

    def public_cursor(self,chat_id):
        row=self.connection.execute("SELECT cursor FROM public_state WHERE chat_id=?",(chat_id,)).fetchone()
        return row[0] if row else 0

    def advance_public_cursor(self,chat_id,message_id):
        with self.connection:
            self.connection.execute(
                "UPDATE public_state SET cursor=MAX(cursor,?) WHERE chat_id=?",
                (message_id,chat_id))

    def should_scan_public(self,chat_id,interval):
        import time
        row=self.connection.execute("SELECT next_scan FROM public_state WHERE chat_id=?",(chat_id,)).fetchone()
        return bool(row and row[0]<=int(time.time()))

    def update_public_status(self,chat_id,error=None,retry_after=1200):
        with self.connection:
            now=int(time.time())
            self.connection.execute(
                "UPDATE public_state SET last_error=?,next_scan=?,checked_at=? WHERE chat_id=?",
                (error,now+retry_after,now,chat_id))

    def public_status(self):
        return list(self.connection.execute(
            """SELECT s.title,CASE WHEN p.client_checked_at>0 THEN p.client_cursor ELSE p.cursor END,p.last_error,p.next_scan,
                      p.checked_at,p.last_success_at,p.last_posts,p.last_new,p.last_matched
               FROM sources s JOIN public_state p ON p.chat_id=s.chat_id
               ORDER BY CASE WHEN p.last_error IS NULL THEN 1 ELSE 0 END, s.title"""
        ))

    def request_manual_scan(self, cooldown_seconds: int = 900):
        """Reschedule due public sources for a single bounded manual scan.

        Never bypass Telegram's backoff or failed-source retry, and rate-limit
        button presses globally across all users and process restarts.
        Returns (scheduled_count, retry_after_seconds).
        """
        cooldown_seconds = max(900, int(cooldown_seconds))
        now = int(time.time())
        with self.connection:
            row=self.connection.execute(
                "SELECT value FROM meta WHERE key='last_manual_scan'"
            ).fetchone()
            last=int(row[0]) if row else 0
            if now < last+cooldown_seconds:
                return 0, last+cooldown_seconds-now
            ready=list(self.connection.execute(
                """SELECT p.chat_id FROM public_state p
                   JOIN sources s ON s.chat_id=p.chat_id
                   WHERE s.enabled=1 AND s.joined=1 AND s.kind='public'
                     AND (p.checked_at=0 OR p.checked_at<=?)
                     AND (p.last_error IS NULL OR p.last_error='')
                """,(now-cooldown_seconds,)
            ))
            if not ready:
                return 0, cooldown_seconds
            for (chat_id,) in ready:
                self.connection.execute(
                    "UPDATE public_state SET next_scan=0 WHERE chat_id=?",(chat_id,))
            self.connection.execute(
                """INSERT INTO meta(key,value) VALUES ('last_manual_scan',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",(str(now),))
            return len(ready),0

    def set_scan_stats(self, chat_id: int, *, posts: int, new_posts: int, matched: int):
        with self.connection:
            now=int(time.time())
            self.connection.execute(
                """UPDATE public_state SET checked_at=?,last_success_at=?,
                   last_posts=?,last_new=?,last_matched=? WHERE chat_id=?""",
                (now,now,posts,new_posts,matched,chat_id)
            )

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


    def recent_posts(self, *, lookback_hours: int = 48, limit: int = 150):
        """Return already discovered public posts for profile re-evaluation."""
        threshold = int(time.time()) - max(1, min(lookback_hours, 168)) * 3600
        return list(self.connection.execute(
            """SELECT c.chat_id,c.message_id,c.title,c.body,c.url,c.posted_at
               FROM castings c JOIN sources s ON s.chat_id=c.chat_id
               WHERE s.enabled=1 AND s.joined=1
                 AND COALESCE(c.posted_at, CAST(strftime('%s',c.created_at) AS INTEGER)) >= ?
               ORDER BY COALESCE(c.posted_at, CAST(strftime('%s',c.created_at) AS INTEGER)) DESC,
                        c.message_id DESC LIMIT ?""",
            (threshold, max(1, min(limit,500))),
        ))

    def record_post(self, chat_id: int, message_id: int, title: str,
                    body: str, url: str | None, posted_at: int | None):
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO castings
                   (chat_id,message_id,title,body,url,posted_at,fingerprint)
                   VALUES (?,?,?,?,?,?,?)""",
                (chat_id,message_id,title,body[:8000],url,posted_at,fingerprint(body[:8000]))
            )

    def queue(self, user_id: int, chat_id: int, message_id: int):
        """Ignore exact-text reposts already queued or sent to the same subscriber.

        Different original source references remain in the archive. Reposting
        identical text is only suppressed within a bounded 14-day window.
        """
        with self.connection:
            row=self.connection.execute(
                "SELECT fingerprint FROM castings WHERE chat_id=? AND message_id=?",
                (chat_id,message_id)
            ).fetchone()
            if not row:
                return
            cutoff=int(time.time())-14*86400
            duplicate=self.connection.execute(
                """SELECT 1 FROM deliveries d JOIN castings c
                   ON c.chat_id=d.chat_id AND c.message_id=d.message_id
                   WHERE d.user_id=? AND c.fingerprint=?
                     AND d.status IN ('pending','sent')
                     AND COALESCE(c.posted_at,
                          CAST(strftime('%s',c.created_at) AS INTEGER))>=?
                   LIMIT 1""",(user_id,row[0],cutoff)
            ).fetchone()
            if duplicate:
                return
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
               WHERE d.status='pending' AND d.attempts<4 AND d.next_retry<=?
                 AND s.active=1
               ORDER BY c.created_at,d.rowid LIMIT ?""",(int(time.time()),limit)
        ).fetchall()

    def count_source_matches(self, chat_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM castings WHERE chat_id=?",(chat_id,)
        ).fetchone()[0])

    def delivery_stats(self):
        return dict(self.connection.execute(
            "SELECT status,COUNT(*) FROM deliveries GROUP BY status"
        ).fetchall())

    def mark_delivery(self, user_id: int, chat_id: int, message_id: int,
                      status: str, error: str | None = None):
        if status not in {"sent","pending","blocked","failed"}:
            raise ValueError("invalid delivery status")
        with self.connection:
            row=self.connection.execute(
                "SELECT attempts FROM deliveries WHERE user_id=? AND chat_id=? AND message_id=?",
                (user_id,chat_id,message_id)
            ).fetchone()
            if row is None:
                return
            attempts=row[0]+1
            if status=="pending" and attempts>=4:
                status="failed"
            next_retry=(int(time.time())+min(3600,60*(2**(attempts-1)))
                        if status=="pending" else 0)
            self.connection.execute(
                """UPDATE deliveries SET status=?,attempts=?,last_error=?,next_retry=?
                   WHERE user_id=? AND chat_id=? AND message_id=?""",
                (status,attempts,error,next_retry,user_id,chat_id,message_id)
            )

    def latest(self, user_id: int, limit: int = 8):
        return self.connection.execute(
            """SELECT c.title,c.body,c.url FROM castings c
               JOIN deliveries d ON c.chat_id=d.chat_id AND c.message_id=d.message_id
               WHERE d.user_id=? AND d.status='sent'
               ORDER BY c.created_at DESC,c.message_id DESC LIMIT ?""",
            (user_id,max(1,min(limit,20)))
        ).fetchall()
