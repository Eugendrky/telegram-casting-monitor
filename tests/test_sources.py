import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.sources import is_source_dialog, source_allowed
from app.store import Store


def dialog(chat_id, name, *, group=True, channel=False, user=False):
    return SimpleNamespace(id=chat_id, name=name, is_group=group,
                           is_channel=channel, is_user=user)


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "seen.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_requires_explicit_selection(self):
        candidate = dialog(-1001, "Кастинги")
        self.assertFalse(source_allowed(candidate, "all", "configured", set()))
        self.assertFalse(source_allowed(candidate, "all", "all", set()))
        self.assertTrue(self.store.toggle_source(-1001, "Кастинги"))
        self.assertTrue(source_allowed(candidate, "all", "selected", self.store.selected_source_ids()))

    def test_source_selection_persists(self):
        self.store.toggle_source(-1001, "Кастинги")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.selected_source_ids(), {-1001})

    def test_reject_legacy_mode(self):
        with self.assertRaises(ValueError):
            self.store.set_source_mode("all")

    def test_skip_private_dialogs(self):
        self.assertFalse(is_source_dialog(dialog(7, "Личное", group=False, user=True)))

    def test_legacy_seen_retained(self):
        self.store.mark(7, 9, True)
        self.store.toggle_source(7, "Источник")
        self.assertTrue(self.store.is_seen(7, 9))


if __name__ == "__main__":
    unittest.main()
