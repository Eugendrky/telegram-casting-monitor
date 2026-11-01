import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.bot_db import Database
from app.cloud_web_reader import CloudWebReader
from app.web_owner_store import WebOwnerStore


class WebOwnerScanRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_source_is_scoped_and_next_source_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "castings.sqlite")
            try:
                owner_id = 101
                db.register(owner_id)
                store = WebOwnerStore(db, owner_id)
                store.consent()
                for name in ("moscowfilms", "kinocekh"):
                    db.add_public(name)
                    store.select_source(name)
                reader = CloudWebReader(
                    db, None, profile_dir=str(Path(folder) / "telegram-web-owner"),
                    owner_store=store, expected_owner=owner_id,
                )
                reader.account_id = AsyncMock(return_value=str(owner_id))
                page = AsyncMock()
                page.evaluate = AsyncMock(side_effect=[
                    True,
                    [
                        {"username": "moscowfilms", "peer": "-101"},
                        {"username": "kinocekh", "peer": "-202"},
                    ],
                    RuntimeError("history failed"),
                    [{"id": 17, "date": int(time.time()), "text": "Кастинг в Москве, ищем актрису 25-35 лет"}],
                ])
                reader._page = page
                await reader.scan_once()
                rows = {row[0]: row for row in store.sources()}
                self.assertEqual(rows["moscowfilms"][5], "Не удалось прочитать сообщения из группы")
                self.assertEqual(rows["kinocekh"][5], None)
                self.assertEqual(rows["kinocekh"][7], 1)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
