from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import WeeklySchedule


@dataclass(frozen=True, slots=True)
class Slot:
    start_ts: int
    end_ts: int
    starts_at: datetime
    ends_at: datetime


def available_slots_for_day(
    day: date,
    *,
    weekly_schedule: WeeklySchedule,
    timezone: ZoneInfo,
    duration_minutes: int,
    buffer_minutes: int = 0,
    busy_intervals: list[tuple[int, int]] | None = None,
    now: datetime | None = None,
) -> list[Slot]:
    """Возвращает незанятые слоты дня в локальном часовом поясе расписания.

    busy_intervals — интервалы, уже расширенные на перерыв между клиентами.
    Предварительная заявка pending удерживает слот, пока тётя не согласует её лично
    или не отметит, что договориться не удалось.
    """
    if duration_minutes <= 0:
        raise ValueError("Длительность услуги должна быть положительной")
    if buffer_minutes < 0:
        raise ValueError("Перерыв между клиентами не может быть отрицательным")

    busy = busy_intervals or []
    current_time = now or datetime.now(timezone)
    if current_time.tzinfo is None:
        raise ValueError("now должен содержать часовой пояс")
    current_time = current_time.astimezone(timezone)

    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=duration_minutes + buffer_minutes)
    slots: list[Slot] = []

    for window_start, window_end in weekly_schedule.get(day.weekday(), []):
        cursor = datetime.combine(day, window_start, tzinfo=timezone)
        closing_time = datetime.combine(day, window_end, tzinfo=timezone)

        while cursor + duration <= closing_time:
            end = cursor + duration
            start_ts = int(cursor.timestamp())
            end_ts = int(end.timestamp())

            if cursor > current_time:
                has_conflict = any(
                    start_ts < busy_end and end_ts > busy_start
                    for busy_start, busy_end in busy
                )
                if not has_conflict:
                    slots.append(Slot(start_ts, end_ts, cursor, end))

            cursor += step

    return slots


def format_day(day: date) -> str:
    weekdays = (
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    )
    months = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    return f"{weekdays[day.weekday()]}, {day.day} {months[day.month - 1]}"


def format_slot(slot: Slot) -> str:
    return f"{slot.starts_at:%H:%M}–{slot.ends_at:%H:%M}"
