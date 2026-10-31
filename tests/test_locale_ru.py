import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver, menu, gender_menu
from app.locale_ru import suggestion_status, readable_time, source_error

class FakeAPI:
    def __init__(self): self.messages=[]
    def send(self,user_id,text,reply_markup=None): self.messages.append((user_id,text,reply_markup))
    def call(self,*args,**kwargs): return True

class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"db.sqlite")
        self.api=FakeAPI()
        self.bot=Receiver(self.api,self.db,999)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def send(self,user_id,message):
        self.bot.handle_private({"from":{"id":user_id},"text":message})
        return self.api.messages[-1][1]
    def test_start_and_help(self):
        text=self.send(123,"/start")
        self.assertIn("Поиск кастингов",text)
        self.assertNotIn("BOT_OWNER_ID",text)
        self.assertNotIn("male|female|any",text)
        self.assertIn("Для администратора",self.send(999,"/start"))
    def test_sources(self):
        self.db.add_public("moscowfilms")
        text=self.send(123,"/sources")
        self.assertIn("Код источника:",text)
        self.assertIn("Для подписки",text)
        self.assertNotIn("ID:",text)
        self.assertIn("числовой код источника",self.send(123,"/follow"))
    def test_suggestion_status(self):
        self.send(123,"/suggest @castinggroup")
        self.assertIn("ожидает проверки",self.send(123,"/suggest @castinggroup"))
        self.assertEqual(suggestion_status("approved"),"одобрен")
        self.assertEqual(suggestion_status("rejected"),"отклонён")
    def test_diagnostics(self):
        self.db.add_public("moscowfilms")
        self.assertIn("Проверка:",self.send(999,"/publicstatus"))
        self.assertEqual(readable_time(None),"ещё не проверялся")
        self.assertIn("ограничил",source_error("rate_limited"))
        self.assertNotIn("pending",self.send(999,"/admin"))
    def test_labels(self):
        output=str(menu())+str(gender_menu())
        for label in ("Предложить канал","Источники","Мой профиль","Мужской","Женский","Любой"):
            self.assertIn(label,output)
