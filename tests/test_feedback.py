import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver

class FakeAPI:
    def __init__(self): self.messages=[]
    def call(self,*args,**kwargs): return True
    def send(self,uid,text,reply_markup=None): self.messages.append((uid,text,reply_markup))

class FeedbackTests(unittest.TestCase):
    def test_feedback_is_owner_scoped_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"bot.db")
            api=FakeAPI()
            bot=Receiver(api,db,999)
            db.register(10)
            db.record_post(-100,5,"Source","Casting female 20-30","https://t.me/cast/5",None)
            db.queue(10,-100,5)
            bot.flush()
            buttons=api.messages[-1][2]["inline_keyboard"][0]
            self.assertIn("feedback:yes:",buttons[0]["callback_data"])
            bot.handle_callback({"from":{"id":20},"data":"feedback:no:-100:5","id":"1"})
            self.assertEqual(db.feedback_stats(),{})
            bot.handle_callback({"from":{"id":10},"data":"feedback:no:-100:5","id":"2"})
            bot.handle_callback({"from":{"id":10},"data":"feedback:no:-100:5","id":"3"})
            self.assertEqual(db.feedback_stats(),{"irrelevant":1})
            bot.handle_callback({"from":{"id":10},"data":"feedback:yes:-100:5","id":"4"})
            self.assertEqual(db.feedback_stats(),{"relevant":1})
            db.close()
