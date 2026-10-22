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


def _parse_id_list(raw: str, *, name: str) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError as exc:
            raise ValueError(f"Неверный ID в {name}: {item!r}") from exc
        if value <= 0:
            raise ValueError(f"ID в {name} должны быть положительными числами")
        values.add(value)
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str | None
    admin_telegram_ids: frozenset[int]
    vk_group_token: str | None
    vk_group_id: int | None
    vk_api_version: str
    admin_vk_user_ids: frozenset[int]
    db_path: Path
    timezone: ZoneInfo
    service_name: str
    service_duration_minutes: int
    buffer_minutes: int
    booking_days_ahead: int
    weekly_schedule: WeeklySchedule
    demo_mode: bool
    max_bot_token: str | None = None
    max_admin_user_ids: frozenset[int] = frozenset()


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip() or None
    vk_group_token = os.getenv("VK_GROUP_TOKEN", "").strip() or None
    vk_group_id_raw = os.getenv("VK_GROUP_ID", "").strip()
    vk_group_id: int | None = None
    if vk_group_id_raw:
        try:
            vk_group_id = int(vk_group_id_raw)
        except ValueError as exc:
            raise ValueError("VK_GROUP_ID должен быть числовым ID сообщества") from exc
        if vk_group_id <= 0:
            raise ValueError("VK_GROUP_ID должен быть положительным числом")

    if bool(vk_group_token) != bool(vk_group_id):
        raise ValueError("Задайте вместе VK_GROUP_TOKEN и VK_GROUP_ID")
    if not bot_token and not vk_group_token:
        raise ValueError(
            "Настройте хотя бы один канал: BOT_TOKEN для Telegram или "
            "VK_GROUP_TOKEN вместе с VK_GROUP_ID для VK."
        )

    admin_telegram_ids = _parse_id_list(
        os.getenv("ADMIN_TELEGRAM_IDS", ""), name="ADMIN_TELEGRAM_IDS"
    )
    admin_vk_user_ids = _parse_id_list(
        os.getenv("VK_ADMIN_USER_IDS", ""), name="VK_ADMIN_USER_IDS"
    )
    max_bot_token = os.getenv("MAX_BOT_TOKEN", "").strip() or None
    max_admin_user_ids = _parse_id_list(
        os.getenv("MAX_ADMIN_USER_IDS", ""), name="MAX_ADMIN_USER_IDS"
    )
    if admin_telegram_ids and not bot_token:
        raise ValueError("ADMIN_TELEGRAM_IDS указан, но BOT_TOKEN не настроен")
    if admin_vk_user_ids and not vk_group_token:
        raise ValueError("VK_ADMIN_USER_IDS указан, но VK-бот не настроен")
    if max_admin_user_ids and not max_bot_token:
        raise ValueError("MAX_ADMIN_USER_IDS указан, но MAX_BOT_TOKEN не настроен")

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
    vk_api_version = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"

    return Settings(
        bot_token=bot_token,
        admin_telegram_ids=admin_telegram_ids,
        vk_group_token=vk_group_token,
        vk_group_id=vk_group_id,
        vk_api_version=vk_api_version,
        admin_vk_user_ids=admin_vk_user_ids,
        db_path=db_path,
        timezone=tz,
        service_name=os.getenv("SERVICE_NAME", "Массаж").strip() or "Массаж",
        service_duration_minutes=duration,
        buffer_minutes=buffer_minutes,
        booking_days_ahead=days_ahead,
        weekly_schedule=parse_weekly_schedule(os.getenv("WEEKLY_SCHEDULE_JSON")),
        demo_mode=demo_mode,
        max_bot_token=max_bot_token,
        max_admin_user_ids=max_admin_user_ids,
    )
