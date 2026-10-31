import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver

class FakeAPI:
    def __init__(self):
        self.messages=[]
    def send(self,uid,text,reply_markup=None):
        self.messages.append((uid,text))

class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite")
        self.api=FakeAPI()
        self.receiver=Receiver(self.api,self.db,999)
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def cmd(self,uid,text):
        self.receiver.handle_private({"from":{"id":uid},"text":text})
        return self.api.messages[-1][1]
    def test_one_user_cannot_change_other_sources(self):
        self.assertIn("ещё не реализовано",self.cmd(101,"/connect"))
        self.assertIn("Сначала",self.cmd(101,"/mysource @moscowfilms"))
        self.cmd(101,"/sources_prepare")
        self.cmd(102,"/sources_prepare")
        self.assertIn("сохранён",self.cmd(101,"/mysource @moscowfilms"))
        self.assertIn("источников нет",self.cmd(102,"/mysources"))
        self.assertIn("@moscowfilms",self.cmd(101,"/mysources"))
        self.cmd(102,"/disconnect")
        self.assertIn("@moscowfilms",self.cmd(101,"/mysources"))
    def test_cleanup_and_restart(self):
        self.cmd(101,"/sources_prepare")
        self.cmd(101,"/mysource @kinocekh")
        second=Receiver(self.api,self.db,999)
        self.assertEqual(second.connection_preferences.sources(101),["kinocekh"])
        self.cmd(101,"/disconnect")
        self.assertFalse(second.connection_preferences.consented(101))
        self.assertEqual(second.connection_preferences.sources(101),[])
    def test_limit_and_duplicate(self):
        self.cmd(101,"/sources_prepare")
        self.cmd(101,"/mysource @kinocekh")
        self.cmd(101,"/mysource @kinocekh")
        self.assertEqual(self.receiver.connection_preferences.sources(101),["kinocekh"])
        self.cmd(101,"/removesource @kinocekh")
        self.assertEqual(self.receiver.connection_preferences.sources(101),[])
    def test_no_account_session_started(self):
        self.cmd(101,"/connect")
        self.assertEqual(self.db.connection.execute("SELECT COUNT(*) FROM user_selected_sources").fetchone()[0],0)
