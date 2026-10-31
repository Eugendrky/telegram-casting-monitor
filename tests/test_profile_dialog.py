import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver

class API:
    def __init__(self):self.sent=[]
    def send(self,uid,text,reply_markup=None):self.sent.append((uid,text,reply_markup))
    def call(self,*args,**kwargs):return True

class ProfileDialogTests(unittest.TestCase):
    def test_age_prompt_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"bot.db"
            db=Database(path);api=API();receiver=Receiver(api,db,515245346)
            receiver.handle_private({"from":{"id":123},"text":"/age"})
            self.assertEqual(db.profile_prompt(123),"age")
            db.close();db=Database(path);receiver=Receiver(api,db,515245346)
            receiver.handle_private({"from":{"id":123},"text":"27"})
            self.assertEqual(db.profile(123)["age"],27)
            self.assertIsNone(db.profile_prompt(123))
            receiver.handle_private({"from":{"id":123},"text":"/age"})
            receiver.handle_private({"from":{"id":123},"text":"abc"})
            self.assertEqual(db.profile_prompt(123),"age")
            receiver.handle_private({"from":{"id":123},"text":"/cancel"})
            self.assertIsNone(db.profile_prompt(123))
            db.close()

if __name__=="__main__":unittest.main()
