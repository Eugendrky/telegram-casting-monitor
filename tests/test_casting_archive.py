import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver

class API:
    def __init__(self): self.messages=[]
    def send(self,user_id,text,reply_markup=None):
        self.messages.append((user_id,text))
    def call(self,*args,**kwargs): return True

class ArchiveTests(unittest.TestCase):
    def test_post_remains_available_after_profile_change(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"db.sqlite"); api=API(); receiver=Receiver(api,db,999)
            sid=db.add_public("moscowfilms")
            receiver.handle_private({"from":{"id":55},"text":"/start"})
            receiver.handle_private({"from":{"id":55},"text":"/age 60"})
            receiver.handle_post({"chat":{"id":sid,"username":"moscowfilms","type":"channel","title":"Москва"},
                "message_id":23,"text":"Кастинг в Москве. Ищем актрису 20-35 лет.","date":None})
            self.assertEqual(len(db.recent_posts()),1)
            self.assertEqual(len(db.pending()),0)
            receiver.handle_private({"from":{"id":55},"text":"/age 30"})
            self.assertGreaterEqual(receiver.refresh_user(55),1)
            self.assertEqual(len(db.pending()),1)
            receiver.refresh_user(55)
            self.assertEqual(len(db.pending()),1)
            db.close()
    def test_status_describes_actual_scan_state(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"db.sqlite");api=API();receiver=Receiver(api,db,999)
            db.add_public("kinocekh")
            receiver.handle_private({"from":{"id":1},"text":"/status"})
            self.assertIn("Веб-проверка проводилась для 0 из 1",api.messages[-1][1])
            db.close()
