import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.credentials import resolve_api_credentials
from app.sources import source_allowed
from app.store import Store


class SafetyTests(unittest.TestCase):
    def test_reject_shared_keys(self):
        with self.assertRaises(ValueError):
            resolve_api_credentials("", "")
        self.assertEqual(resolve_api_credentials("12345", "private-hash"),
                         (12345, "private-hash", False))

    def test_all_mode_never_authorizes_everything(self):
        dialog = SimpleNamespace(id=-1001, is_user=False, is_group=False, is_channel=True)
        self.assertFalse(source_allowed(dialog, "all", "all", set()))
        self.assertTrue(source_allowed(dialog, "all", "all", {-1001}))

    def test_cursor_persists_and_never_moves_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            store = Store(path)
            self.assertEqual(store.cursor(-1001), 0)
            store.advance_cursor(-1001, 10)
            store.advance_cursor(-1001, 9)
            store.close()
            store = Store(path)
            self.assertEqual(store.cursor(-1001), 10)
            store.close()

    def test_empty_selection_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "store.db")
            self.assertEqual(store.source_mode(), "selected")
            self.assertEqual(store.selected_sources(), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
