from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from bisnesbbot.database import AppointmentRepository, SlotUnavailableError


class AppointmentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AppointmentRepository(
            Path(self.temp_dir.name) / "appointments.sqlite3"
        )
        self.repository.initialize()
        self.start_ts = int(datetime(2030, 1, 7, 10, 0, tzinfo=UTC).timestamp())
        self.end_ts = int(datetime(2030, 1, 7, 11, 0, tzinfo=UTC).timestamp())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_request(self, *, channel: str = "telegram"):
        return self.repository.create_pending(
            client_name="Анна",
            contact_method="phone",
            contact_value="+7 999 123-45-67",
            service_name="Массаж",
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            source_channel=channel,
            external_user_id="123",
            external_chat_id="123",
            client_username="anna" if channel == "telegram" else None,
        )

    def test_new_request_starts_pending_and_blocks_slot(self) -> None:
        appointment = self.create_request()
        self.assertEqual(appointment.status, "pending")
        self.assertEqual(appointment.contact_method, "phone")
        self.assertEqual(appointment.contact_value, "+7 999 123-45-67")
        with self.assertRaises(SlotUnavailableError):
            self.create_request()

    def test_existing_database_is_migrated_without_losing_appointments(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                service_name TEXT NOT NULL,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                source_channel TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                external_chat_id TEXT NOT NULL,
                client_username TEXT,
                created_at INTEGER NOT NULL,
                decided_at INTEGER,
                decided_by TEXT
            );
            INSERT INTO appointments (
                client_name, service_name, start_ts, end_ts, status,
                source_channel, external_user_id, external_chat_id,
                client_username, created_at
            ) VALUES ('Старая заявка', 'Массаж', 100, 200, 'pending',
                      'telegram', '1', '1', NULL, 100);
            """
        )
        connection.close()

        legacy_repository = AppointmentRepository(legacy_path)
        legacy_repository.initialize()
        migrated = legacy_repository.get(1)

        self.assertIsNotNone(migrated)
        self.assertEqual(migrated.client_name, "Старая заявка")
        self.assertEqual(migrated.contact_method, "")
        self.assertEqual(migrated.contact_value, "")

    def test_same_slot_is_shared_across_channels(self) -> None:
        vk_appointment = self.create_request(channel="vk")
        self.assertEqual(vk_appointment.source_channel, "vk")
        with self.assertRaises(SlotUnavailableError):
            self.create_request(channel="telegram")

    def test_declined_request_releases_slot(self) -> None:
        first = self.create_request()
        declined = self.repository.decide(first.id, status="declined", admin_id="999")
        self.assertIsNotNone(declined)
        self.assertEqual(declined.status, "declined")

        second = self.create_request()
        self.assertEqual(second.status, "pending")

    def test_confirmed_request_keeps_slot_blocked(self) -> None:
        appointment = self.create_request()
        confirmed = self.repository.decide(
            appointment.id, status="confirmed", admin_id="999"
        )
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.status, "confirmed")
        with self.assertRaises(SlotUnavailableError):
            self.create_request()

    def test_only_one_admin_decision_can_win(self) -> None:
        appointment = self.create_request()
        first = self.repository.decide(
            appointment.id, status="confirmed", admin_id="999"
        )
        second = self.repository.decide(
            appointment.id, status="declined", admin_id="888"
        )
        self.assertEqual(first.status, "confirmed")
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
