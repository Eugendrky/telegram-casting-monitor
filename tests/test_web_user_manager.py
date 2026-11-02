import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.bot_db import Database
from app.web_user_manager import WebUserManager


class WebUserManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite")
        self.manager = WebUserManager(self.db, Mock(), 900, root=self.temp.name)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_username_allowlist_binds_to_immutable_telegram_id(self):
        self.manager.allow("@Actor_Name")
        self.assertFalse(self.manager.is_allowed(101, "other_name"))
        self.assertTrue(self.manager.is_allowed(101, "actor_name"))
        self.assertTrue(self.manager.is_allowed(101, "renamed_actor"))
        self.assertFalse(self.manager.is_allowed(202, "actor_name"))

    def test_owner_bypasses_allowlist_but_non_owner_requires_username(self):
        self.assertTrue(self.manager.is_allowed(900, None))
        self.manager.allow("actor_name")
        self.assertFalse(self.manager.is_allowed(101, None))
        self.assertTrue(self.manager.is_allowed(101, "actor_name"))

    def test_deny_removes_binding_and_reports_unlisted_names(self):
        self.manager.allow("actor_name")
        self.assertTrue(self.manager.is_allowed(101, "actor_name"))
        name, user_id, removed = self.manager.deny("@actor_name")
        self.assertEqual((name, user_id, removed), ("actor_name", 101, True))
        self.assertFalse(self.manager.is_allowed(101, "actor_name"))
        self.assertEqual(self.manager.deny("actor_name"), ("actor_name", None, False))

    def test_each_user_gets_an_isolated_profile_directory(self):
        owner = self.manager.get(900)
        actor = self.manager.get(101)
        self.assertNotEqual(owner.reader.profile_dir, actor.reader.profile_dir)
        self.assertEqual(owner.reader.profile_dir.name, "telegram-web-owner")
        self.assertEqual(actor.reader.profile_dir, Path(self.temp.name) / "telegram-web-users" / "101")

    def test_capacity_is_bounded(self):
        manager = WebUserManager(self.db, Mock(), 900, root=self.temp.name, max_users=1)
        manager._reserve(900)
        with self.assertRaisesRegex(ValueError, "максимальное число аккаунтов"):
            manager._reserve(101)


if __name__ == "__main__":
    unittest.main()
