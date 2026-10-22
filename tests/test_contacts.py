from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bisnesbbot.config import DEMO_WEEKLY_SCHEDULE, Settings
from bisnesbbot.contacts import (
    CONTACT_METHOD_LABELS,
    normalize_client_name,
    normalize_contact_value,
    normalize_phone_number,
    preliminary_summary,
)


class ContactFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            bot_token=None,
            admin_telegram_ids=frozenset(),
            vk_group_token="test-token",
            vk_group_id=123,
            vk_api_version="5.199",
            admin_vk_user_ids=frozenset(),
            db_path=Path("test.sqlite3"),
            timezone=ZoneInfo("Europe/Moscow"),
            service_name="Массаж",
            service_duration_minutes=60,
            buffer_minutes=0,
            booking_days_ahead=14,
            weekly_schedule=DEMO_WEEKLY_SCHEDULE,
            demo_mode=True,
        )

    def test_phone_is_entered_before_selecting_a_contact_channel(self) -> None:
        self.assertEqual(normalize_client_name("  Анна\nПетрова "), "Анна Петрова")
        self.assertEqual(
            normalize_phone_number(" +7 (999) 123-45-67 "),
            "+7 (999) 123-45-67",
        )
        self.assertEqual(
            normalize_contact_value("vk", "+7 (999) 123-45-67"),
            "+7 (999) 123-45-67",
        )
        self.assertEqual(
            set(CONTACT_METHOD_LABELS),
            {"vk", "max", "phone"},
        )

    def test_invalid_phone_and_unknown_contact_method_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_phone_number("1234")
        with self.assertRaises(ValueError):
            normalize_contact_value("telegram", "+7 999 123-45-67")
        self.assertNotIn("whatsapp", CONTACT_METHOD_LABELS)

    def test_preview_says_request_is_preliminary_and_shows_phone_and_method(
        self,
    ) -> None:
        start_ts = int(datetime(2030, 1, 7, 10, 0, tzinfo=UTC).timestamp())
        end_ts = start_ts + 3600
        preview = preliminary_summary(
            client_name="Анна",
            start_ts=start_ts,
            end_ts=end_ts,
            contact_method="max",
            contact_value="+7 999 123-45-67",
            settings=self.settings,
        )

        self.assertIn("предварительной заявки", preview)
        self.assertIn("Номер телефона: +7 999 123-45-67", preview)
        self.assertIn("Как связаться: MAX", preview)
        self.assertIn("лично обсудить", preview)


if __name__ == "__main__":
    unittest.main()
