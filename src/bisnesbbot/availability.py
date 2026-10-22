from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .config import Settings
from .database import AppointmentRepository
from .schedule import Slot, available_slots_for_day


def slots_for_day(
    day: date,
    *,
    settings: Settings,
    repository: AppointmentRepository,
    now: datetime | None = None,
) -> list[Slot]:
    start_of_day = datetime.combine(day, time.min, tzinfo=settings.timezone)
    end_of_day = start_of_day + timedelta(days=1)
    buffer_seconds = settings.buffer_minutes * 60
    busy = repository.get_busy_intervals(
        range_start_ts=int(start_of_day.timestamp()),
        range_end_ts=int(end_of_day.timestamp()),
        buffer_seconds=buffer_seconds,
    )
    return available_slots_for_day(
        day,
        weekly_schedule=settings.weekly_schedule,
        timezone=settings.timezone,
        duration_minutes=settings.service_duration_minutes,
        buffer_minutes=settings.buffer_minutes,
        busy_intervals=busy,
        now=now,
    )


def available_days(
    *,
    settings: Settings,
    repository: AppointmentRepository,
    now: datetime | None = None,
) -> list[tuple[date, list[Slot]]]:
    local_now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone)
    today = local_now.date()
    results: list[tuple[date, list[Slot]]] = []
    for offset in range(settings.booking_days_ahead + 1):
        day = today + timedelta(days=offset)
        slots = slots_for_day(
            day, settings=settings, repository=repository, now=local_now
        )
        if slots:
            results.append((day, slots))
    return results


def date_is_in_booking_horizon(
    day: date,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    today = (
        (now or datetime.now(settings.timezone)).astimezone(settings.timezone).date()
    )
    return today <= day <= today + timedelta(days=settings.booking_days_ahead)


def schedule_notice(settings: Settings) -> str:
    if not settings.demo_mode:
        return ""
    return (
        "\n\n⚠️ Сейчас показано условное тестовое расписание. "
        "Любая заявка предварительная: день и время нужно лично согласовать с тётей."
    )
