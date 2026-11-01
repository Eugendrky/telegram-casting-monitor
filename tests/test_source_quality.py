import tempfile
import time
import unittest
from pathlib import Path
from app.bot_db import Database
from app.post_identity import fingerprint
from app.public_channels import parse_preview, PreviewError
from app.bot_runtime import Receiver

class API:
    def __init__(self): self.messages=[]
    def send(self,uid,text,reply_markup=None): self.messages.append((uid,text,reply_markup))
    def call(self,*args,**kwargs): return True

class QualityTests(unittest.TestCase):
    def test_exact_repost_only_once_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"bot.db"; db=Database(path)
            a=db.add_public("moscowfilms"); b=db.add_public("kinocekh")
            db.register(101)
            body="Кастинг в Москве. Ищем девушку 25-35 лет."
            now=int(time.time())
            db.record_post(a,11,"A",body,"https://t.me/moscowfilms/11",now)
            db.record_post(b,22,"B",body,"https://t.me/kinocekh/22",now)
            db.queue(101,a,11); db.mark_delivery(101,a,11,"sent")
            db.queue(101,b,22)
            self.assertEqual(db.delivery_stats().get("sent"),1)
            self.assertEqual(len(db.pending()),0)
            db.close()
            db=Database(path)
            self.assertEqual(db.delivery_stats().get("sent"),1)
            db.close()
    def test_distinct_role_still_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite")
            sid=db.add_public("moscowfilms");db.register(101)
            db.record_post(sid,11,"A","Кастинг: актриса 25 лет",None,None)
            db.record_post(sid,12,"A","Кастинг: актриса 35 лет",None,None)
            db.queue(101,sid,11);db.queue(101,sid,12)
            self.assertEqual(len(db.pending()),2)
            db.close()
    def test_inaccessible_group_raises_actionable_error(self):
        html='<html><div class="tgme_page_extra">2 531 members</div></html>'
        with self.assertRaisesRegex(PreviewError,"Telegram-группа"):
            parse_preview(html,"moscowfilms")
    def test_owner_diagnostic_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite");db.add_public("kinocekh")
            api=API();bot=Receiver(api,db,999)
            bot.handle_callback({"from":{"id":999},"data":"admin:sources","id":"1"})
            self.assertIn("Проверка источников",api.messages[-1][1])
            db.close()
