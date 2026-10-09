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

    def test_existing_configuration_used_before_first_selection(self):
        self.assertEqual(self.store.source_mode(), "configured")
        self.assertTrue(source_allowed(dialog(-1001, "Объявления"), "all", "configured", set()))
        self.assertFalse(source_allowed(dialog(45, "Личный чат", user=True, group=False),
                                        "all", "configured", set()))

    def test_selected_only_filters_and_is_persistent(self):
        self.assertTrue(self.store.toggle_source(-1001, "Кастинги"))
        self.assertEqual(self.store.source_mode(), "selected")
        self.assertEqual(self.store.selected_source_ids(), {-1001})
        self.store.close()
        self.store = Store(self.path)
        ids = self.store.selected_source_ids()
        self.assertEqual(ids, {-1001})
        self.assertTrue(source_allowed(dialog(-1001, "Кастинги"), "all", "selected", ids))
        self.assertFalse(source_allowed(dialog(-1002, "Другой канал", group=False, channel=True),
                                        "all", "selected", ids))
        self.assertFalse(source_allowed(dialog(-1001, "Личная переписка", user=True, group=False),
                                        "all", "selected", ids))

    def test_empty_selection_scans_nothing(self):
        self.store.set_source_mode("selected")
        self.assertFalse(source_allowed(dialog(-1001, "Новости"), "all", "selected", set()))

    def test_switching_to_all_preserves_selection(self):
        self.store.toggle_source(-1001, "Кастинги")
        self.store.set_source_mode("all")
        self.assertTrue(source_allowed(dialog(-1002, "Новости"), "title", "all", set()))
        self.assertEqual(self.store.selected_source_ids(), {-1001})
        self.store.set_source_mode("selected")
        self.assertEqual(self.store.selected_source_ids(), {-1001})
        self.assertFalse(self.store.toggle_source(-1001, "Кастинги"))
        self.assertEqual(self.store.selected_source_ids(), set())

    def test_reject_invalid_mode(self):
        with self.assertRaises(ValueError):
            self.store.set_source_mode("everything")

    def test_existing_seen_messages_survive_migration(self):
        self.store.mark(7, 9, True)
        self.store.toggle_source(7, "Источник")
        self.assertTrue(self.store.is_seen(7, 9))
        self.store.close()
        self.store = Store(self.path)
        self.assertTrue(self.store.is_seen(7, 9))

    def test_candidate_groups_and_channels_only(self):
        self.assertTrue(is_source_dialog(dialog(1, "Группа")))
        self.assertTrue(is_source_dialog(dialog(2, "Канал", group=False, channel=True)))
        self.assertFalse(is_source_dialog(dialog(3, "Пользователь", group=False, user=True)))


if __name__ == "__main__":
    unittest.main()