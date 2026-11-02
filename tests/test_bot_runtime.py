import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver, link_for, parse_setting

class FakeAPI:
    def __init__(self): self.messages=[]
    def send(self, chat_id, text, reply_markup=None): self.messages.append((chat_id,text,reply_markup))

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"bot.db")
        self.api=FakeAPI()
        self.bot=Receiver(self.api,self.db,999)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_source_requires_approval(self):
        source={"id":-100123,"type":"channel","title":"Castings","username":"casts"}
        msg={"chat":source,"message_id":8,"text":"Кастинг девушка 20-30 лет, Москва","date":1}
        self.bot.handle_private({"from":{"id":101},"text":"/start"})
        self.bot.handle_post(msg)
        self.assertEqual(self.db.pending(),[])
        self.db.enable_source(-100123,True)
        self.bot.handle_post({**msg,"message_id":9})
        self.assertEqual(len(self.db.pending()),1)
        self.bot.flush()
        self.assertEqual(len(self.db.latest(101)),1)
        self.bot.handle_post({**msg,"message_id":9})
        self.assertEqual(len(self.db.latest(101)),1)

    def test_member_event_admin_enables(self):
        event={"chat":{"id":-123,"type":"group","title":"Group"},
               "new_chat_member":{"status":"administrator"}}
        self.bot.handle_membership(event)
        self.assertEqual(self.db.source(-123)[3],0)
        self.bot.handle_private({"from":{"id":999},"text":"/enable -123"})
        self.assertEqual(self.db.source(-123)[3],1)

    def test_profiles_and_subscriptions(self):
        self.db.set_source(-44,"Castings",None,"group",True)
        self.db.enable_source(-44,True)
        self.bot.handle_private({"from":{"id":101},"text":"/start"})
        self.bot.handle_private({"from":{"id":101},"text":"/city Казань"})
        self.bot.handle_private({"from":{"id":101},"text":"/age 24"})
        self.assertEqual(self.db.profile(101)["age"],24)
        self.bot.handle_private({"from":{"id":101},"text":"/unfollow -44"})
        self.assertEqual(self.db.subscribers(-44),[])
        self.bot.handle_private({"from":{"id":101},"text":"/follow -44"})
        self.assertEqual(len(self.db.subscribers(-44)),1)

    def test_invalid_settings(self):
        self.assertIsNone(parse_setting("/gender","invalid"))
        self.assertIsNone(parse_setting("/age","1000"))
        self.assertEqual(parse_setting("/height","170"),("height_cm",170))

    def test_channel_permalink(self):
        self.assertEqual(link_for({"id":-100123,"username":"casts"},7),"https://t.me/casts/7")

    def test_start_installs_persistent_menu_button(self):
        self.bot.handle_private({"from":{"id":101},"text":"/start"})
        markup=self.api.messages[-2][2]
        self.assertEqual(markup["keyboard"],[[{"text":"📋 Меню"}]])
        self.assertTrue(markup["is_persistent"])

    def test_menu_button_reopens_inline_menu(self):
        self.bot.handle_private({"from":{"id":101},"text":"📋 Меню"})
        self.assertIn("inline_keyboard",self.api.messages[-1][2])
