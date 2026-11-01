import tempfile
import unittest
from pathlib import Path
from app.public_channels import normalize_channel, parse_preview, source_id, PreviewError
from app.bot_db import Database
from app.bot_runtime import Receiver


HTML = """
<html><body>
<div class="tgme_widget_message" data-post="castings_moscow/120">
  <div class="tgme_widget_message_text">Кастинг! Ищем девушку 22-30 лет на съемки в Москве</div>
  <time datetime="2026-10-30T10:00:00+00:00"></time>
</div>
<div class="tgme_widget_message" data-post="castings_moscow/121">
  <div class="tgme_widget_message_text">Кастинг. Нужны мужчины 45-50 лет</div>
  <time datetime="2026-10-31T10:00:00+00:00"></time>
</div></body></html>
"""


class FakeBot:
    def __init__(self):
        self.sent = []
    def send(self, user_id, text, reply_markup=None):
        self.sent.append((user_id, text))


class PreviewTests(unittest.TestCase):
    def test_parse_public_posts_and_order(self):
        posts = parse_preview(HTML, "castings_moscow")
        self.assertEqual([p.message_id for p in posts], [120, 121])
        self.assertEqual(posts[0].url, "https://t.me/castings_moscow/120")
        self.assertIsInstance(posts[0].timestamp, int)

    def test_invalid_names_rejected(self):
        for name in ("https://t.me/+private", "https://example.org/post", "../admin", "@a"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    normalize_channel(name)

    def test_source_id_is_stable_and_unique(self):
        self.assertEqual(source_id("CASTINGS_MOSCOW"), source_id("castings_moscow"))
        self.assertNotEqual(source_id("castings_moscow"), source_id("casting_other"))

    def test_empty_preview_is_reported(self):
        with self.assertRaises(PreviewError):
            parse_preview("<html>empty</html>", "castings_moscow")

    def test_owner_adds_public_channel_and_user_can_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "db.sqlite")
            api = FakeBot()
            receiver = Receiver(api, db, 100)
            receiver.handle_private({"from": {"id": 100}, "text": "/addsource https://t.me/castings_moscow"})
            self.assertEqual(len(db.public_sources()), 1)
            source = db.public_sources()[0]
            self.assertEqual(source[5], "castings_moscow")
            receiver.handle_private({"from": {"id": 500}, "text": "/start"})
            receiver.handle_private({"from": {"id": 500}, "text": "/gender female"})
            receiver.handle_post({
                "chat": {"id": source[0], "type": "channel", "title": source[1], "username": source[5]},
                "message_id": 120, "text": "Кастинг в Москве, девушка 22-30 лет",
            })
            self.assertEqual(len(db.pending()), 1)
            receiver.flush()
            self.assertEqual(len(db.latest(500)), 1)
            db.advance_public_cursor(source[0], 120)
            self.assertEqual(db.public_cursor(source[0]), 120)
            db.close()


if __name__ == "__main__":
    unittest.main()
