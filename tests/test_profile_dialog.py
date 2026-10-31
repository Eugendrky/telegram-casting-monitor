import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver, parse_setting, gender_menu

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


    def test_russian_gender_and_buttons(self):
        self.assertEqual(parse_setting("/gender","Женский"),("gender","female"))
        self.assertEqual(parse_setting("/gender","Мужской"),("gender","male"))
        self.assertEqual(parse_setting("/gender","Любой"),("gender","any"))
        self.assertEqual(parse_setting("/gender","female"),("gender","female"))
        self.assertIsNone(parse_setting("/gender","неизвестно"))
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"bot.db"); api=API()
            receiver=Receiver(api,db,515245346)
            receiver.handle_private({"from":{"id":123},"text":"/gender"})
            self.assertEqual(db.profile_prompt(123),"gender")
            receiver.handle_callback({"from":{"id":123},"data":"gender:set:female","id":"abc"})
            self.assertEqual(db.profile(123)["gender"],"female")
            self.assertIsNone(db.profile_prompt(123))
            self.assertIn("Женский",api.sent[-1][1])
            self.assertEqual(gender_menu()["inline_keyboard"][0][0]["text"],"👨 Мужской")
            db.close()

if __name__=="__main__":unittest.main()
