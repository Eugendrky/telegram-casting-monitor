import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.bot_db import Database
from app.bot_http import BotAPI,PNG_SIGNATURE
from app.bot_runtime import Receiver

class FakeAPI:
    def __init__(self):self.messages=[]
    def send(self,uid,text,reply_markup=None):self.messages.append((uid,text))

class SelfTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"bot.db")
        self.api=FakeAPI()
        self.receiver=Receiver(self.api,self.db,999)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def test_owner_selftest_does_not_modify_data(self):
        before=self.db.admin_counts()
        self.receiver.handle_private({"from":{"id":999},"text":"/selftest"})
        self.assertIn("✅ Успешно",self.api.messages[-1][1])
        self.assertEqual(before,self.db.admin_counts())
    def test_other_user_cannot_use_owner_selftest(self):
        self.receiver.handle_private({"from":{"id":123},"text":"/selftest"})
        self.assertEqual(self.api.messages,[])
    def test_real_png_signature_is_accepted(self):
        self.assertEqual(PNG_SIGNATURE,bytes.fromhex("89504e470d0a1a0a"))
        with patch("urllib.request.urlopen") as transport:
            transport.return_value.__enter__.return_value.read.return_value=b'{"ok":true,"result":{}}'
            BotAPI("test").send_photo(999,PNG_SIGNATURE+b"payload")
        self.assertTrue(transport.called)
        with self.assertRaises(ValueError):
            BotAPI("test").send_photo(999,b"not a png")

if __name__=="__main__":
    unittest.main()
