import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.quality_guardian import inspect, render, save_snapshot, read_snapshot, claim_alert, filter_fixtures

class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"db.sqlite")
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_no_sources_is_reported(self):
        result=inspect(self.db,now=100000)
        self.assertEqual(result["sources"]["enabled"],0)
        self.assertIn("Не включён ни один",render(result))
    def test_unavailable_source_not_zero_castings(self):
        sid=self.db.add_public("moscowfilms")
        self.db.update_public_status(sid,error="Нет публичного просмотра")
        result=inspect(self.db)
        self.assertEqual(result["sources"]["unavailable"],1)
        self.assertTrue(result["findings"])
        save_snapshot(self.db,result)
        self.assertEqual(read_snapshot(self.db)["sources"]["unavailable"],1)
    def test_alert_is_rate_limited_across_calls(self):
        self.assertTrue(claim_alert(self.db,100000))
        self.assertFalse(claim_alert(self.db,100001))
        self.assertTrue(claim_alert(self.db,200000))
    def test_filter_fixture(self):
        passed,total=filter_fixtures()
        self.assertEqual(passed,total)

if __name__=="__main__":
    unittest.main()
