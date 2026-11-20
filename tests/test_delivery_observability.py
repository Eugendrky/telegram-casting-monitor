import tempfile
import time
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver
from app.bot_http import TelegramError

class API:
    def __init__(self):
        self.messages=[]
        self.fail=True
    def send(self,uid,text,reply_markup=None):
        if self.fail: raise TelegramError(502,"temporary")
        self.messages.append((uid,text,reply_markup))
    def call(self,*args,**kwargs): return True

class RetryTests(unittest.TestCase):
    def test_pending_item_rechecked_after_gender_change(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "db.sqlite")
            self.addCleanup(db.close)
            api = API()
            api.fail = False
            receiver = Receiver(api, db, 999)
            sid = db.add_public("vsekastingi")
            db.update_profile(7, "age", 38)
            receiver.handle_post({"chat": {"id": sid}, "message_id": 26785,
                                  "text": "Кастинг в Москве. Парень 35–40 лет"})
            self.assertEqual(len(db.pending()), 1)
            db.update_profile(7, "gender", "female")
            receiver.flush()
            self.assertFalse(api.messages)
            self.assertFalse(db.pending())

    def test_migration_and_bounded_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"test.sqlite"
            db=Database(path)
            db.register(7)
            db.record_post(-111,15,"Channel","Кастинг: ищем актрису","https://t.me/cast/15",None)
            db.queue(7,-111,15)
            api=API()
            receiver=Receiver(api,db,999)
            receiver.flush()
            self.assertEqual(len(db.pending()),0)
            self.assertEqual(db.delivery_stats().get("pending"),1)
            db.connection.execute("UPDATE deliveries SET next_retry=0")
            db.connection.commit()
            api.fail=False
            receiver.flush()
            self.assertEqual(db.delivery_stats().get("sent"),1)
            self.assertEqual(len(api.messages),1)
            self.assertEqual(len(db.pending()),0)
            chat_id=db.add_public("moscowfilms")
            db.set_scan_stats(chat_id,posts=18,new_posts=3,matched=1)
            x=db.public_status()[0]
            self.assertEqual(x[6:9],(18,3,1))
            db.close()
            db=Database(path)
            self.assertEqual(db.public_status()[0][6:9],(18,3,1))
            db.close()

if __name__=="__main__": unittest.main()
