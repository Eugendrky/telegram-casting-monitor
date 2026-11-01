"""Per-user casting data and encrypted Telegram-session storage interface.

Only approved public sources may be selected. Sessions are encrypted by the
calling authentication manager; this module never logs or returns them to bots.
"""
from __future__ import annotations
import time
from app.public_channels import normalize_channel
from app.post_identity import fingerprint

class PersonalStore:
    def __init__(self,db):
        self.db=db
        with db._lock,db.connection:
            db.connection.executescript("""
                CREATE TABLE IF NOT EXISTS personal_sessions (
                  user_id INTEGER PRIMARY KEY,
                  encrypted_value BLOB NOT NULL,
                  created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS personal_sources (
                  user_id INTEGER NOT NULL,
                  username TEXT NOT NULL,
                  last_id INTEGER NOT NULL DEFAULT 0,
                  next_scan INTEGER NOT NULL DEFAULT 0,
                  last_checked INTEGER NOT NULL DEFAULT 0,
                  last_success INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT,
                  PRIMARY KEY(user_id,username)
                );
                CREATE TABLE IF NOT EXISTS personal_castings (
                  user_id INTEGER NOT NULL,
                  username TEXT NOT NULL,
                  message_id INTEGER NOT NULL,
                  body TEXT NOT NULL,
                  link TEXT NOT NULL,
                  posted_at INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'archived',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  next_retry INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(user_id,username,message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_personal_pending
                   ON personal_castings(status,next_retry);
            """)

    def save_session(self,user_id:int,encrypted_value:bytes):
        if user_id<=0 or not isinstance(encrypted_value,bytes) or len(encrypted_value)<20:
            raise ValueError("invalid authorized session")
        with self.db._lock,self.db.connection:
            self.db.connection.execute(
                """INSERT INTO personal_sessions(user_id,encrypted_value,created_at)
                   VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE
                   SET encrypted_value=excluded.encrypted_value,created_at=excluded.created_at""",
                (user_id,encrypted_value,int(time.time())))

    def session(self,user_id:int) -> bytes | None:
        with self.db._lock:
            result=self.db.connection.execute(
                "SELECT encrypted_value FROM personal_sessions WHERE user_id=?",(user_id,)).fetchone()
        return result[0] if result else None

    def connected_users(self):
        with self.db._lock:
            return [int(row[0]) for row in self.db.connection.execute(
                "SELECT user_id FROM personal_sessions ORDER BY user_id")]

    def select_source(self,user_id:int,raw:str) -> str:
        username=normalize_channel(raw)
        if self.session(user_id) is None:
            raise ValueError("Сначала подключите аккаунт")
        if not any(item[5]==username and item[3] and item[4]
                   for item in self.db.public_sources()):
            raise ValueError("Источник не согласован администратором")
        with self.db._lock,self.db.connection:
            self.db.connection.execute(
                "INSERT OR IGNORE INTO personal_sources(user_id,username) VALUES(?,?)",
                (user_id,username))
        return username

    def remove_source(self,user_id:int,raw:str)->bool:
        username=normalize_channel(raw)
        with self.db._lock,self.db.connection:
            removed=self.db.connection.execute(
                "DELETE FROM personal_sources WHERE user_id=? AND username=?",
                (user_id,username)).rowcount
        return bool(removed)

    def sources(self,user_id:int):
        with self.db._lock:
            return list(self.db.connection.execute(
                """SELECT username,last_id,next_scan,last_checked,last_success,last_error
                   FROM personal_sources WHERE user_id=? ORDER BY username""",(user_id,)))

    def due_sources(self,user_id:int,now:int|None=None):
        now=int(time.time()) if now is None else now
        return [row for row in self.sources(user_id) if row[2]<=now]

    def update_source(self,user_id:int,username:str,*,cursor:int|None=None,
                      error:str|None=None,interval:int=1800,checked_at:int|None=None):
        now=int(time.time()) if checked_at is None else int(checked_at)
        with self.db._lock,self.db.connection:
            self.db.connection.execute(
                """UPDATE personal_sources SET
                   last_id=CASE WHEN ? IS NULL THEN last_id ELSE MAX(last_id,?) END,
                   next_scan=?,last_checked=?,
                   last_success=CASE WHEN ? IS NULL THEN ? ELSE last_success END,
                   last_error=?
                   WHERE user_id=? AND username=?""",
                (cursor,cursor,now+max(900,int(interval)),now,error,now,error,user_id,username))

    def save_casting(self,user_id:int,username:str,message_id:int,body:str,
                     posted_at:int,*,matched:bool):
        username=normalize_channel(username)
        if self.session(user_id) is None:
            return False
        if message_id<=0 or not body.strip() or posted_at<=0:
            return False
        content=body.strip()[:8000]
        key=fingerprint(content)
        cutoff=int(time.time())-14*86400
        with self.db._lock,self.db.connection:
            if matched:
                duplicate=self.db.connection.execute(
                    """SELECT 1 FROM personal_castings WHERE user_id=?
                       AND fingerprint=? AND status IN ('pending','sent')
                       AND posted_at>=? LIMIT 1""",(user_id,key,cutoff)).fetchone()
                if duplicate:
                    matched=False
            result=self.db.connection.execute(
                """INSERT OR IGNORE INTO personal_castings
                   (user_id,username,message_id,body,link,posted_at,fingerprint,status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (user_id,username,message_id,content,
                 f"https://t.me/{username}/{message_id}",posted_at,key,
                 "pending" if matched else "archived"))
        return bool(result.rowcount)

    def pending(self,limit=10,now:int|None=None):
        stamp=int(time.time()) if now is None else int(now)
        with self.db._lock:
            return list(self.db.connection.execute(
                """SELECT user_id,username,message_id,body,link FROM personal_castings
                   WHERE status='pending' AND attempts<4 AND next_retry<=?
                   ORDER BY posted_at,username,message_id LIMIT ?""",
                (stamp,max(1,min(limit,25)))))

    def mark_delivery(self,user_id:int,username:str,message_id:int,status:str):
        if status not in ("sent","pending","blocked"):
            raise ValueError("invalid delivery")
        with self.db._lock,self.db.connection:
            old=self.db.connection.execute(
                """SELECT attempts FROM personal_castings WHERE
                   user_id=? AND username=? AND message_id=?""",
                (user_id,username,message_id)).fetchone()
            if old is None:
                return
            attempts=old[0]+1
            if status=="pending" and attempts>=4:
                status="failed"
            delay=int(time.time())+min(3600,60*2**min(attempts,5)) if status=="pending" else 0
            self.db.connection.execute(
                """UPDATE personal_castings SET status=?,attempts=?,next_retry=?
                   WHERE user_id=? AND username=? AND message_id=?""",
                (status,attempts,delay,user_id,username,message_id))

    def latest(self,user_id:int,limit=8):
        with self.db._lock:
            return list(self.db.connection.execute(
                """SELECT username,body,link FROM personal_castings
                   WHERE user_id=? AND status='sent'
                   ORDER BY posted_at DESC LIMIT ?""",
                (user_id,max(1,min(limit,20)))))

    def forget(self,user_id:int):
        with self.db._lock,self.db.connection:
            for table in ("personal_castings","personal_sources","personal_sessions"):
                self.db.connection.execute("DELETE FROM "+table+" WHERE user_id=?",(user_id,))

    def stats(self,user_id:int):
        with self.db._lock:
            return dict(self.db.connection.execute(
                """SELECT status,COUNT(*) FROM personal_castings
                   WHERE user_id=? GROUP BY status""",(user_id,)))
