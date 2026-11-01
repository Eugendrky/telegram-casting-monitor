import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.bot_db import Database
from app.cloud_web_reader import CloudWebReader
from app.web_owner_store import WebOwnerStore

class WebOwnerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"test.sqlite")
        self.db.register(101)
        self.db.register(202)
        self.db.update_profile(101,"city","Москва")
        self.db.update_profile(101,"gender","female")
        self.db.update_profile(101,"age",30)
        self.owner=WebOwnerStore(self.db,101)
        self.db.add_public("moscowfilms")
        self.owner.consent()
        self.owner.select_source("moscowfilms")
    async def asyncTearDown(self):
        self.db.close()
        self.temp.cleanup()
    async def test_no_public_subscriber_leak(self):
        now=int(time.time())
        self.owner.add_post("moscowfilms",17,
            "Кастинг в Москве, ищем актрису 25-35 лет",now)
        self.assertEqual(len(self.db.pending()),0)
        row=self.owner.claim_next()
        self.assertEqual(row[1],17)
        self.owner.mark_delivery("moscowfilms",17,"sent")
        self.assertEqual(len(self.owner.latest()),1)
        self.assertEqual(self.db.latest(202),[])
    async def test_identity_rejected_before_any_history_request(self):
        reader=CloudWebReader(self.db,None,profile_dir=str(Path(self.temp.name)/"telegram-web-owner"),
                              owner_store=self.owner,expected_owner=101)
        reader.account_id=AsyncMock(return_value="202")
        reader._page=AsyncMock()
        with self.assertRaisesRegex(RuntimeError,"does not match"):
            await reader.scan_once()
        reader._page.evaluate.assert_not_called()
    async def test_only_three_approved_sources(self):
        with self.assertRaises(ValueError):
            self.owner.select_source("someothergroup")
    async def test_forget_preserves_other_bot_users(self):
        self.owner.forget()
        self.assertFalse(self.owner.consented())
        self.assertEqual(self.owner.sources(),[])
        self.assertIsNotNone(self.db.profile(202))
    async def test_delivery_does_not_requeue_sent_after_restart(self):
        stamp=int(time.time())
        self.owner.add_post("moscowfilms",33,
           "Кастинг в Москве. Ищем актрису 25-35 лет",stamp)
        self.assertIsNotNone(self.owner.claim_next())
        self.owner.mark_delivery("moscowfilms",33,"sent")
        again=WebOwnerStore(self.db,101)
        self.assertIsNone(again.claim_next())
if __name__=="__main__":
    unittest.main()
