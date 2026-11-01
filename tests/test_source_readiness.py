import tempfile
import unittest
from pathlib import Path

from app.bot_db import Database
from app.bot_runtime import Receiver
from app.source_readiness import readiness_text,empty_castings_text

class API:
    def __init__(self): self.sent=[]
    def send(self,uid,text,reply_markup=None): self.sent.append((uid,text))

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"database.sqlite")
        self.api=API()
        self.receiver=Receiver(self.api,self.db,999)
        self.ids=[self.db.add_public(x) for x in ("moscowfilms","kinocekh","castingiot4000r")]
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def mark_all_unavailable(self):
        for uid in self.ids:
            self.db.update_public_status(uid,error="Публичное превью недоступно")
    def test_never_claim_zero_when_sources_unavailable(self):
        self.mark_all_unavailable()
        self.receiver.handle_private({"from":{"id":10},"text":"/castings"})
        self.assertIn("нет подтверждённого доступа",self.api.sent[-1][1])
    def test_ready_reports_all_three_failures(self):
        self.mark_all_unavailable()
        self.receiver.handle_private({"from":{"id":10},"text":"/ready"})
        result=self.api.sent[-1][1]
        self.assertIn("Недоступны: 3",result)
        self.assertIn("@moscowfilms",result)
        self.assertIn("Авторизация Telegram Web: не подключена",result)
    def test_no_scan_when_all_sources_in_backoff(self):
        self.mark_all_unavailable()
        self.receiver.handle_private({"from":{"id":10},"text":"/scan"})
        self.assertIn("сейчас недоступны",self.api.sent[-1][1])
    def test_empty_not_yet_checked_differs_from_absence(self):
        text=readiness_text(self.db)
        self.assertIn("Ещё не проверен",text)
        self.assertIn("не подтверждено",text)
        self.assertIn("источникам",empty_castings_text(self.db))
if __name__=="__main__":
    unittest.main()
