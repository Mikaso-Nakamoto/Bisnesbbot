from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from bisnesbbot.schedule import available_slots_for_day


class AvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.day = date(2024, 1, 1)  # понедельник
        self.tz = ZoneInfo("UTC")
        self.schedule = {0: [(time(10, 0), time(12, 0))]}
        self.now = datetime(2023, 12, 31, 12, 0, tzinfo=UTC)

    def test_generates_slots_with_configured_duration(self) -> None:
        slots = available_slots_for_day(
            self.day,
            weekly_schedule=self.schedule,
            timezone=self.tz,
            duration_minutes=60,
            now=self.now,
        )
        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in slots], ["10:00", "11:00"]
        )

    def test_pending_or_confirmed_interval_hides_slot(self) -> None:
        busy_start = int(datetime(2024, 1, 1, 10, 0, tzinfo=UTC).timestamp())
        busy_end = int(datetime(2024, 1, 1, 11, 0, tzinfo=UTC).timestamp())
        slots = available_slots_for_day(
            self.day,
            weekly_schedule=self.schedule,
            timezone=self.tz,
            duration_minutes=60,
            busy_intervals=[(busy_start, busy_end)],
            now=self.now,
        )
        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in slots], ["11:00"]
        )

    def test_skips_slots_in_the_past(self) -> None:
        now = datetime(2024, 1, 1, 10, 30, tzinfo=UTC)
        slots = available_slots_for_day(
            self.day,
            weekly_schedule=self.schedule,
            timezone=self.tz,
            duration_minutes=60,
            now=now,
        )
        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in slots], ["11:00"]
        )

    def test_buffer_is_added_between_slots(self) -> None:
        slots = available_slots_for_day(
            self.day,
            weekly_schedule={0: [(time(10, 0), time(12, 30))]},
            timezone=self.tz,
            duration_minutes=60,
            buffer_minutes=15,
            now=self.now,
        )
        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in slots], ["10:00", "11:15"]
        )


if __name__ == "__main__":
    unittest.main()
