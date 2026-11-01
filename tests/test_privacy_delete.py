import tempfile
import time
import unittest
from pathlib import Path

from app.bot_db import Database
from app.bot_runtime import Receiver

class API:
    def __init__(self): self.messages=[]
    def send(self,uid,text,reply_markup=None):
        self.messages.append((uid,text,reply_markup))
    def call(self,*args,**kwargs): return True

class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"db.sqlite")
        self.api=API()
        self.receiver=Receiver(self.api,self.db,999)
        self.sid=self.db.add_public("moscowfilms")
        for uid in (111,222):
            self.db.register(uid)
            self.db.update_profile(uid,"city","Москва")
            self.db.follow(uid,self.sid,True)
        self.db.record_post(self.sid,42,"A","Кастинг: ищем актрису 25 лет",
                            "https://t.me/moscowfilms/42",int(time.time()))
        for uid in (111,222):
            self.db.queue(uid,self.sid,42)
            self.db.mark_delivery(uid,self.sid,42,"sent")
    def tearDown(self): self.db.close();self.tmp.cleanup()
    def test_delete_one_account_preserves_other_and_posts(self):
        self.receiver.handle_private({"from":{"id":111},"text":"/delete_me"})
        self.assertIsNotNone(self.db.profile(111))
        self.receiver.handle_callback({"id":"c1","from":{"id":111},
                                       "data":"privacy:confirm"})
        self.assertIsNone(self.db.profile(111))
        self.assertEqual(self.db.latest(111),[])
        self.assertIsNotNone(self.db.profile(222))
        self.assertEqual(len(self.db.latest(222)),1)
        self.assertEqual(self.db.count_source_matches(self.sid),1)
    def test_cancel_preserves_data(self):
        self.receiver.handle_callback({"id":"c2","from":{"id":111},
                                       "data":"privacy:cancel"})
        self.assertIsNotNone(self.db.profile(111))
    def test_invalid_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.db.delete_user_data(0)
if __name__=="__main__":
    unittest.main()
