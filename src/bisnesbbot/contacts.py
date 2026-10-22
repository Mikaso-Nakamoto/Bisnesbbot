from __future__ import annotations

import re
from datetime import datetime

from .config import Settings
from .schedule import format_day

CONTACT_METHOD_LABELS = {
    "phone": "Звонок по телефону",
    "vk": "ВКонтакте",
    "max": "MAX",
}

# Сохраняем читаемость заявок, созданных до изменения сценария.
LEGACY_CONTACT_METHOD_LABELS = {"telegram": "Telegram (старая заявка)"}

PHONE_PROMPT = (
    "Как с вами связаться? Напишите свой номер телефона. "
    "После этого выберите, как тёте удобнее связаться с вами: VK, MAX или звонком."
)

CONTACT_METHOD_PROMPT = "Выберите, как тёте связаться с вами:"


def normalize_client_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 2 <= len(name) <= 80:
        raise ValueError("Имя должно содержать от 2 до 80 символов.")
    return name


def contact_method_label(method: str) -> str:
    if method in CONTACT_METHOD_LABELS:
        return CONTACT_METHOD_LABELS[method]
    if method in LEGACY_CONTACT_METHOD_LABELS:
        return LEGACY_CONTACT_METHOD_LABELS[method]
    raise ValueError("Неизвестный способ связи.")


def normalize_phone_number(raw: str) -> str:
    value = " ".join(raw.split())
    if not 3 <= len(value) <= 40:
        raise ValueError("Номер телефона должен содержать от 3 до 40 символов.")
    if not re.fullmatch(r"\+?[\d\s().-]+", value):
        raise ValueError("В номере телефона используйте цифры и знаки + ( ) -.")
    digits = sum(character.isdigit() for character in value)
    if not 7 <= digits <= 15:
        raise ValueError("Проверьте номер: в нём должно быть от 7 до 15 цифр.")
    return value


def normalize_contact_value(method: str, raw: str) -> str:
    if method not in CONTACT_METHOD_LABELS:
        raise ValueError("Выберите способ связи: телефон, VK или MAX.")
    return normalize_phone_number(raw)


def preliminary_summary(
    *,
    client_name: str,
    start_ts: int,
    end_ts: int,
    contact_method: str,
    contact_value: str,
    settings: Settings,
) -> str:
    starts_at = datetime.fromtimestamp(start_ts, settings.timezone)
    ends_at = datetime.fromtimestamp(end_ts, settings.timezone)
    return (
        "Проверьте данные перед отправкой предварительной заявки:\n"
        f"Имя: {client_name}\n"
        f"День и время: {format_day(starts_at.date())}, "
        f"{starts_at:%H:%M}–{ends_at:%H:%M}\n"
        f"Номер телефона: {contact_value}\n"
        f"Как связаться: {contact_method_label(contact_method)}\n\n"
        "⚠️ Это только предварительная заявка, не подтверждённая запись. "
        "Тётя свяжется с вами по указанному номеру или выбранному каналу, "
        "чтобы лично обсудить и согласовать время."
    )
