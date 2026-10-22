from __future__ import annotations

import json
from datetime import date
from typing import Any

from .schedule import Slot, format_day, format_slot

VK_DATE_PAGE_SIZE = 6


def _button(
    label: str,
    action: str,
    *,
    color: str = "primary",
    **payload: Any,
) -> dict[str, Any]:
    data = {"action": action, **payload}
    return {
        "action": {
            "type": "callback",
            "label": label[:40],
            "payload": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        },
        "color": color,
    }


def _keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"inline": True, "one_time": False, "buttons": rows}


def main_keyboard(*, is_admin: bool) -> dict[str, Any]:
    rows = [[_button("📅 Оставить заявку", "book:choose", color="primary")]]
    if is_admin:
        rows.append(
            [_button("📥 Заявки для согласования", "admin:pending", color="secondary")]
        )
    return _keyboard(rows)


def date_keyboard(
    days: list[tuple[date, list[Slot]]], *, offset: int
) -> dict[str, Any]:
    page = days[offset : offset + VK_DATE_PAGE_SIZE]
    rows: list[list[dict[str, Any]]] = []
    for index in range(0, len(page), 2):
        rows.append(
            [
                _button(
                    format_day(day),
                    "book:date",
                    color="secondary",
                    date=day.isoformat(),
                )
                for day, _ in page[index : index + 2]
            ]
        )

    navigation: list[dict[str, Any]] = []
    if offset > 0:
        navigation.append(
            _button(
                "⬅️ Раньше",
                "book:choose",
                color="secondary",
                offset=max(0, offset - VK_DATE_PAGE_SIZE),
            )
        )
    if offset + VK_DATE_PAGE_SIZE < len(days):
        navigation.append(
            _button(
                "Позже ➡️",
                "book:choose",
                color="secondary",
                offset=offset + VK_DATE_PAGE_SIZE,
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append([_button("↩️ В меню", "menu:main", color="secondary")])
    return _keyboard(rows)


def slot_keyboard(slots: list[Slot]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for index in range(0, len(slots), 2):
        rows.append(
            [
                _button(
                    format_slot(slot),
                    "book:slot",
                    color="secondary",
                    start_ts=slot.start_ts,
                )
                for slot in slots[index : index + 2]
            ]
        )
    rows.append([_button("↩️ К датам", "book:choose", color="secondary")])
    return _keyboard(rows)


def contact_method_keyboard() -> dict[str, Any]:
    return _keyboard(
        [
            [_button("ВКонтакте", "contact:select", color="secondary", method="vk")],
            [_button("MAX", "contact:select", color="secondary", method="max")],
            [
                _button(
                    "📞 Телефонный звонок",
                    "contact:select",
                    color="secondary",
                    method="phone",
                )
            ],
        ]
    )


def cancel_keyboard() -> dict[str, Any]:
    return _keyboard([[_button("↩️ Отмена", "flow:cancel", color="secondary")]])


def preliminary_confirmation_keyboard() -> dict[str, Any]:
    return _keyboard(
        [
            [
                _button(
                    "✅ Отправить предварительную заявку",
                    "booking:submit",
                    color="positive",
                )
            ],
            [
                _button(
                    "✏️ Изменить способ связи",
                    "booking:change_contact",
                    color="secondary",
                )
            ],
            [
                _button(
                    "📞 Изменить номер телефона",
                    "booking:change_phone",
                    color="secondary",
                )
            ],
            [_button("❌ Отменить", "booking:cancel", color="negative")],
        ]
    )


def admin_keyboard(appointment_id: int) -> dict[str, Any]:
    return _keyboard(
        [
            [
                _button(
                    "✅ Договорились",
                    "admin:confirm",
                    color="positive",
                    appointment_id=appointment_id,
                ),
                _button(
                    "❌ Не договорились",
                    "admin:decline",
                    color="negative",
                    appointment_id=appointment_id,
                ),
            ]
        ]
    )


def parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
