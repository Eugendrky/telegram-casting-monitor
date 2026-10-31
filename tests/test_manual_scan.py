import tempfile
import time
import unittest
from pathlib import Path

from app.bot_db import Database
from app.bot_runtime import Receiver


class API:
    def __init__(self): self.messages=[]
    def send(self, user_id, text, reply_markup=None): self.messages.append((user_id,text,reply_markup))
    def call(self, *args, **kwargs): return True


class ScanTests(unittest.TestCase):
    def test_manual_scan_deduplication_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"app.sqlite"
            db=Database(path)
            sid=db.add_public("moscowfilms")
            api=API()
            bot=Receiver(api,db,999)
            bot.handle_private({"from":{"id":15},"text":"/scan"})
            self.assertIn("Запланирована проверка",api.messages[-1][1])
            self.assertTrue(db.should_scan_public(sid,1800))
            bot.handle_callback({"from":{"id":15},"data":"scan:now","id":"2"})
            self.assertIn("пока недоступна",api.messages[-1][1])
            db.close()
            db=Database(path)
            self.assertGreater(db.request_manual_scan()[1],0)
            db.close()

    def test_scan_does_not_reset_sent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"bot.sqlite")
            sid=db.add_public("kinocekh")
            db.register(15)
            db.record_post(sid,22,"Channel","Кастинг, ищем актрису",
                           "https://t.me/kinocekh/22",int(time.time()))
            db.queue(15,sid,22)
            db.mark_delivery(15,sid,22,"sent")
            db.request_manual_scan()
            db.queue(15,sid,22)
            self.assertEqual(db.delivery_stats().get("sent"),1)
            self.assertEqual(len(db.pending()),0)
            db.close()
