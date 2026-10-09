import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import Profile, Settings
from app.control import ScanState, settings_text, status_text


def _settings() -> Settings:
    return Settings(
        api_id=123, api_hash="hash", session="session",
        profile=Profile(city="Москва", gender="male", age=28, height_cm=180, kids_ok=False),
        scan_mode="all", lookback_hours=36, messages_per_chat=40,
        scan_interval_minutes=20, max_notify_per_cycle=25, chat_delay_seconds=0.8,
        sqlite_path=Path("/tmp/seen.db"), notify_peer="me", port=8080,
        log_level="INFO", bot_token="bot-token", bot_owner_id=1234,
    )


class BotTextTests(unittest.TestCase):
    def test_status_before_any_run(self) -> None:
        text = status_text(ScanState(), _settings())
        self.assertIn("ещё не запускался", text)
        self.assertIn("20 мин", text)

    def test_status_during_scan(self) -> None:
        state = ScanState(running=True, cycles=2, last_started=datetime.now(timezone.utc), last_sent=3)
        text = status_text(state, _settings())
        self.assertIn("идёт поиск", text)
        self.assertIn("Проверок: 2", text)

    def test_settings_are_read_only_in_first_iteration(self) -> None:
        text = settings_text(_settings())
        self.assertIn("Москва", text)
        self.assertIn("мужской", text)
        self.assertIn("Изменение параметров", text)


if __name__ == "__main__":
    unittest.main()
