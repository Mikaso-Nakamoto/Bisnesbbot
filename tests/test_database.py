from __future__ import annotations

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

    def create_request(self):
        return self.repository.create_pending(
            client_name="Анна",
            service_name="Массаж",
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            source_channel="telegram",
            external_user_id="123",
            external_chat_id="123",
            client_username="anna",
        )

    def test_new_request_starts_pending_and_blocks_slot(self) -> None:
        appointment = self.create_request()
        self.assertEqual(appointment.status, "pending")
        with self.assertRaises(SlotUnavailableError):
            self.create_request()

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
