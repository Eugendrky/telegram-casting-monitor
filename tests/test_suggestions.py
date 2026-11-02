import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from app.bot_db import Database
from app.bot_runtime import Receiver

class FakeAPI:
    def __init__(self): self.messages=[]
    def send(self,uid,text,reply_markup=None): self.messages.append((uid,text,reply_markup))
    def call(self,*args,**kwargs):return True

class SuggestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite")
        self.api=FakeAPI()
        self.bot=Receiver(self.api,self.db,999)
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def test_suggest_button_and_approval(self):
        self.bot.handle_private({"from":{"id":101},"text":"/start"})
        self.bot.handle_callback({"id":"1","from":{"id":101},"data":"suggest:start"})
        self.bot.handle_private({"from":{"id":101},"text":"https://t.me/moscowfilms"})
        self.assertEqual(len(self.db.pending_suggestions()),1)
        self.assertEqual(len(self.db.public_sources()),0)
        proposal=self.db.pending_suggestions()[0][0]
        self.bot.handle_callback({"id":"2","from":{"id":101},"data":"review:yes:{}".format(proposal)})
        self.assertEqual(len(self.db.public_sources()),0)
        self.bot.handle_callback({"id":"3","from":{"id":999},"data":"review:yes:{}".format(proposal)})
        self.assertEqual(len(self.db.public_sources()),1)
        self.assertEqual(len(self.db.pending_suggestions()),0)
        self.bot.handle_callback({"id":"4","from":{"id":999},"data":"review:yes:{}".format(proposal)})
        self.assertEqual(len(self.db.public_sources()),1)
    def test_reject_and_duplicate(self):
        self.bot.handle_private({"from":{"id":25},"text":"/suggest @kinocekh"})
        self.bot.handle_private({"from":{"id":25},"text":"/suggest @kinocekh"})
        self.assertEqual(len(self.db.pending_suggestions()),1)
        proposal=self.db.pending_suggestions()[0][0]
        self.bot.handle_callback({"id":"5","from":{"id":999},"data":"review:no:{}".format(proposal)})
        self.assertEqual(len(self.db.public_sources()),0)
    def test_duplicate_source_explains_pending_web_access(self):
        self.db.add_public("castingiot4000r")
        self.bot.web_pilot=SimpleNamespace(store=SimpleNamespace(sources=lambda: [
            ("castingiot4000r",0,0,100,0,
             "Источник не найден среди первых 500 чатов Telegram Web",0,0,0)
        ]))
        self.bot.handle_private({"from":{"id":999},"text":"/suggest @castingiot4000r"})
        reply=self.api.messages[-1][1]
        self.assertIn("уже включён в каталог",reply)
        self.assertIn("заявка на вступление ещё рассматривается",reply)
        self.assertIn("/websource @castingiot4000r",reply)
    def test_admin_is_private(self):
        self.bot.handle_private({"from":{"id":101},"text":"/admin"})
        self.assertFalse(any(text.startswith("🛡") for _,text,_ in self.api.messages))
        self.bot.handle_private({"from":{"id":999},"text":"/admin"})
        self.assertTrue(any(text.startswith("🛡") for _,text,_ in self.api.messages))

if __name__=="__main__":
    unittest.main()
