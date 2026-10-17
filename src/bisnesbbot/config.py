from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ScheduleWindow = tuple[time, time]
WeeklySchedule = dict[int, list[ScheduleWindow]]

# Это только безопасное демо-расписание для проверки сценария. Оно не является
# расписанием массажиста и должно быть заменено перед реальной публикацией бота.
DEMO_WEEKLY_SCHEDULE: WeeklySchedule = {
    0: [(time(10, 0), time(18, 0))],
    1: [(time(10, 0), time(18, 0))],
    2: [(time(10, 0), time(18, 0))],
    3: [(time(10, 0), time(18, 0))],
    4: [(time(10, 0), time(18, 0))],
    5: [(time(10, 0), time(14, 0))],
    6: [],
}


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "да"}:
        return True
    if normalized in {"0", "false", "no", "off", "нет"}:
        return False
    raise ValueError(f"{name} должен быть true/false, получено: {value!r}")


def _parse_clock(value: object) -> time:
    if not isinstance(value, str):
        raise TypeError("Время в расписании должно быть строкой вида HH:MM")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Неверное время в расписании: {value!r}; используйте HH:MM"
        ) from exc


def parse_weekly_schedule(raw: str | None) -> WeeklySchedule:
    """Читает расписание из JSON: ключи 0..6, значения — пары начала и конца."""
    if not raw or not raw.strip():
        return {day: list(windows) for day, windows in DEMO_WEEKLY_SCHEDULE.items()}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "WEEKLY_SCHEDULE_JSON должен содержать корректный JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise TypeError("WEEKLY_SCHEDULE_JSON должен быть JSON-объектом")

    parsed: WeeklySchedule = {day: [] for day in range(7)}
    for raw_day, raw_windows in payload.items():
        try:
            day = int(raw_day)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"День недели должен быть числом от 0 до 6: {raw_day!r}"
            ) from exc
        if day not in range(7):
            raise ValueError(f"День недели должен быть от 0 до 6: {day}")
        if not isinstance(raw_windows, list):
            raise TypeError(f"Расписание для дня {day} должно быть списком интервалов")

        for raw_window in raw_windows:
            if not isinstance(raw_window, list) or len(raw_window) != 2:
                raise ValueError(
                    f'Интервал для дня {day} должен выглядеть как ["10:00", "18:00"]'
                )
            start, end = _parse_clock(raw_window[0]), _parse_clock(raw_window[1])
            if start >= end:
                raise ValueError(
                    f"Начало интервала должно быть раньше конца: {raw_window!r}"
                )
            parsed[day].append((start, end))

        parsed[day].sort(key=lambda window: window[0])
        for previous, current in zip(parsed[day], parsed[day][1:]):
            if current[0] < previous[1]:
                raise ValueError(f"Интервалы расписания пересекаются для дня {day}")

    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_telegram_ids: frozenset[int]
    db_path: Path
    timezone: ZoneInfo
    service_name: str
    service_duration_minutes: int
    buffer_minutes: int
    booking_days_ahead: int
    weekly_schedule: WeeklySchedule
    demo_mode: bool


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и добавьте токен бота."
        )

    admin_ids: set[int] = set()
    for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            admin_ids.add(int(item))
        except ValueError as exc:
            raise ValueError(f"Неверный ID в ADMIN_TELEGRAM_IDS: {item!r}") from exc

    timezone_name = os.getenv("TIMEZONE", "Europe/Moscow").strip()
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Неизвестный часовой пояс TIMEZONE={timezone_name!r}"
        ) from exc

    try:
        duration = int(os.getenv("SERVICE_DURATION_MINUTES", "60"))
        buffer_minutes = int(os.getenv("BUFFER_MINUTES", "0"))
        days_ahead = int(os.getenv("BOOKING_DAYS_AHEAD", "14"))
    except ValueError as exc:
        raise ValueError(
            "Длительность, перерыв и горизонт записи должны быть целыми числами"
        ) from exc

    if not 5 <= duration <= 720:
        raise ValueError("SERVICE_DURATION_MINUTES должен быть от 5 до 720")
    if not 0 <= buffer_minutes <= 240:
        raise ValueError("BUFFER_MINUTES должен быть от 0 до 240")
    if not 1 <= days_ahead <= 90:
        raise ValueError("BOOKING_DAYS_AHEAD должен быть от 1 до 90")

    demo_mode = _parse_bool(os.getenv("DEMO_MODE", "true"), name="DEMO_MODE")
    db_path = Path(os.getenv("DB_PATH", "data/bisnesbbot.sqlite3")).expanduser()

    return Settings(
        bot_token=bot_token,
        admin_telegram_ids=frozenset(admin_ids),
        db_path=db_path,
        timezone=tz,
        service_name=os.getenv("SERVICE_NAME", "Массаж").strip() or "Массаж",
        service_duration_minutes=duration,
        buffer_minutes=buffer_minutes,
        booking_days_ahead=days_ahead,
        weekly_schedule=parse_weekly_schedule(os.getenv("WEEKLY_SCHEDULE_JSON")),
        demo_mode=demo_mode,
    )
