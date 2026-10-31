"""User connection choices, no Telegram credentials or sessions."""
from __future__ import annotations
import time

class ConnectionPreferences:
    def __init__(self, db):
        self.db=db

    def grant(self, user_id:int):
        if not isinstance(user_id,int) or user_id<=0:
            raise ValueError("invalid user")
        with self.db.connection:
            self.db.connection.execute(
                """INSERT INTO connection_preferences(user_id,consented_at)
                   VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET
                   consented_at=excluded.consented_at""",(user_id,int(time.time())))
    def revoke(self,user_id:int):
        with self.db.connection:
            self.db.connection.execute("DELETE FROM connection_preferences WHERE user_id=?",(user_id,))
    def consented(self,user_id:int)->bool:
        return bool(self.db.connection.execute(
            "SELECT 1 FROM connection_preferences WHERE user_id=?",(user_id,)).fetchone())

    def select_source(self,user_id:int,source:str):
        from app.public_channels import normalize_channel
        name=normalize_channel(source)
        if not self.consented(user_id):
            raise ValueError("consent required")
        with self.db.connection:
            self.db.connection.execute(
                "INSERT OR IGNORE INTO user_selected_sources(user_id,username) VALUES (?,?)",
                (user_id,name))
        return name
    def sources(self,user_id:int):
        return [row[0] for row in self.db.connection.execute(
            "SELECT username FROM user_selected_sources WHERE user_id=? ORDER BY username",(user_id,))]
    def clear(self,user_id:int):
        with self.db.connection:
            self.db.connection.execute("DELETE FROM user_selected_sources WHERE user_id=?",(user_id,))
            self.db.connection.execute("DELETE FROM connection_preferences WHERE user_id=?",(user_id,))
