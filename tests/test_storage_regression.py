import tempfile
import unittest
from pathlib import Path

from app.store import Store


class StorageRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "castings.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_custom_source_resolved_and_removed(self):
        self.store.add_custom_source("casting_channel")
        self.assertEqual(len(self.store.monitored_sources()), 1)
        self.store.update_custom_peer("casting_channel", chat_id=-100123,
            title="Casting channel", peer_id=123, access_hash=456, peer_kind="channel")
        source = self.store.monitored_sources()[0]
        self.assertEqual(source["chat_id"], -100123)
        self.assertEqual(source["access_hash"], 456)
        self.store.close()
        self.store = Store(self.db)
        self.assertEqual(self.store.monitored_sources()[0]["peer_id"], 123)
        self.store.remove_custom_source("casting_channel")
        self.assertEqual(self.store.monitored_sources(), [])

    def test_selected_and_custom_sources_are_both_included(self):
        self.store.toggle_source(-1009, "Selected", peer_id=9,
                                 access_hash=11, peer_kind="channel")
        self.store.add_custom_source("another_channel")
        sources = self.store.monitored_sources()
        self.assertEqual({s["kind"] for s in sources}, {"selected", "custom"})

    def test_castings_are_saved_without_duplicates(self):
        self.store.record_casting(-101, 42, "Casting", "Ищем актрису",
                                  "https://t.me/channel/42", 5, "роль")
        self.store.record_casting(-101, 42, "Casting", "Дублируем",
                                  "https://t.me/channel/42", 5, "роль")
        rows = self.store.latest_castings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Ищем актрису")
        self.assertEqual(rows[0][5], 0)
        self.store.mark_delivered(-101, 42)
        self.assertEqual(self.store.latest_castings()[0][5], 1)
        self.store.close()
        self.store = Store(self.db)
        self.assertEqual(len(self.store.latest_castings()), 1)


if __name__ == "__main__":
    unittest.main()
