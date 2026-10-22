from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .availability import (
    available_days,
    date_is_in_booking_horizon,
    schedule_notice,
    slots_for_day,
)
from .config import Settings
from .contacts import (
    CONTACT_METHOD_LABELS,
    CONTACT_METHOD_PROMPT,
    PHONE_PROMPT,
    normalize_client_name,
    normalize_contact_value,
    normalize_phone_number,
    preliminary_summary,
)
from .database import AppointmentRepository, SlotUnavailableError
from .notifications import NotificationService, format_appointment
from .schedule import format_day
from .vk import VKClient
from .vk_ui import (
    VK_DATE_PAGE_SIZE,
    admin_keyboard,
    cancel_keyboard,
    contact_method_keyboard,
    date_keyboard,
    main_keyboard,
    parse_payload,
    preliminary_confirmation_keyboard,
    slot_keyboard,
)

logger = logging.getLogger(__name__)


class VKBookingBot:
    """Основной VK-интерфейс предварительной заявки на запись."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: AppointmentRepository,
        vk_client: VKClient,
        notifications: NotificationService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.vk_client = vk_client
        self.notifications = notifications
        self._flows: dict[tuple[int, int], dict[str, Any]] = {}

    async def run(self) -> None:
        async for event in self.vk_client.updates():
            try:
                await self.handle_event(event)
            except Exception:
                logger.exception("Не удалось обработать событие VK")

    async def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "message_new":
            await self._handle_message_new(event)
        elif event_type == "message_event":
            await self._handle_message_event(event)

    async def _handle_message_new(self, event: dict[str, Any]) -> None:
        event_object = event.get("object")
        if not isinstance(event_object, dict):
            return
        message = event_object.get("message", event_object)
        if not isinstance(message, dict) or message.get("out"):
            return

        try:
            user_id = int(message["from_id"])
            peer_id = int(message["peer_id"])
        except (KeyError, TypeError, ValueError):
            return
        if user_id <= 0:
            return
        if peer_id != user_id:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Для предварительной заявки напишите сообществу в личные сообщения.",
            )
            return

        state_key = (peer_id, user_id)
        text = str(message.get("text", "")).strip()
        normalized = text.casefold()
        payload = parse_payload(message.get("payload"))
        action = payload.get("action")
        if isinstance(action, str):
            await self._handle_action(action, payload, peer_id=peer_id, user_id=user_id)
            return

        if normalized in {"/start", "start", "начать", "/начать", "привет"}:
            self._flows.pop(state_key, None)
            await self._send_home(peer_id=peer_id, user_id=user_id)
            return
        if normalized in {"/myid", "myid", "мой id", "id"}:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text=(
                    f"Ваш VK ID: {user_id}\n"
                    "Для настройки администратора добавьте этот номер в "
                    "VK_ADMIN_USER_IDS в файле .env."
                ),
            )
            return
        if normalized in {"/cancel", "cancel", "отмена"}:
            self._flows.pop(state_key, None)
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Черновик предварительной заявки отменён.",
                keyboard=main_keyboard(
                    is_admin=user_id in self.settings.admin_vk_user_ids
                ),
            )
            return
        if normalized in {"/pending", "pending", "заявки"}:
            await self._send_pending(peer_id=peer_id, user_id=user_id)
            return

        flow = self._flows.get(state_key)
        if flow is not None:
            stage = flow.get("stage")
            if stage == "name":
                await self._receive_name(text, peer_id=peer_id, user_id=user_id)
            elif stage == "contact_value":
                await self._receive_contact_value(
                    text, peer_id=peer_id, user_id=user_id
                )
            elif stage == "contact_method":
                await self.vk_client.send_message(
                    peer_id=peer_id,
                    text="Выберите способ связи одной из кнопок:",
                    keyboard=contact_method_keyboard(),
                )
            elif stage == "confirm":
                await self._send_preliminary_preview(peer_id=peer_id, flow=flow)
            return

        if normalized in {"📅 оставить заявку", "оставить заявку", "записаться"}:
            await self._show_dates(peer_id=peer_id, offset=0)
            return
        await self._send_home(peer_id=peer_id, user_id=user_id)

    async def _handle_message_event(self, event: dict[str, Any]) -> None:
        event_object = event.get("object")
        if not isinstance(event_object, dict):
            return
        try:
            user_id = int(event_object["user_id"])
            peer_id = int(event_object["peer_id"])
            event_id = str(event_object["event_id"])
        except (KeyError, TypeError, ValueError):
            return

        result_text = "Готово."
        try:
            if user_id != peer_id:
                result_text = "Откройте личный диалог с сообществом."
            else:
                payload = parse_payload(event_object.get("payload"))
                action = payload.get("action")
                if not isinstance(action, str):
                    result_text = "Не удалось определить действие кнопки."
                else:
                    result_text = await self._handle_action(
                        action, payload, peer_id=peer_id, user_id=user_id
                    )
        except Exception:
            logger.exception("Не удалось выполнить действие кнопки VK")
            result_text = "Не удалось обработать действие. Попробуйте ещё раз."
        finally:
            try:
                await self.vk_client.answer_callback(
                    event_id=event_id,
                    user_id=user_id,
                    peer_id=peer_id,
                    text=result_text,
                )
            except Exception:
                logger.exception("Не удалось подтвердить нажатие callback-кнопки VK")

    async def _handle_action(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        peer_id: int,
        user_id: int,
    ) -> str:
        state_key = (peer_id, user_id)
        if action == "menu:main":
            self._flows.pop(state_key, None)
            await self._send_home(peer_id=peer_id, user_id=user_id)
            return "Открыто меню."

        if action == "book:choose":
            try:
                offset = max(0, int(payload.get("offset", 0)))
            except (TypeError, ValueError):
                offset = 0
            await self._show_dates(peer_id=peer_id, offset=offset)
            return "Показал доступные даты."

        if action == "book:date":
            await self._show_slots(payload.get("date"), peer_id=peer_id)
            return "Показал свободное время."

        if action == "book:slot":
            await self._select_slot(
                payload.get("start_ts"), peer_id=peer_id, user_id=user_id
            )
            return "Выбрано время."

        if action == "contact:select":
            return await self._select_contact_method(
                payload.get("method"), peer_id=peer_id, user_id=user_id
            )

        if action == "booking:change_contact":
            flow = self._flows.get(state_key)
            if flow is None or flow.get("stage") != "confirm":
                return "Черновик устарел. Начните заново."
            flow["contact_method"] = None
            flow["stage"] = "contact_method"
            await self.vk_client.send_message(
                peer_id=peer_id,
                text=CONTACT_METHOD_PROMPT,
                keyboard=contact_method_keyboard(),
            )
            return "Выберите способ связи."

        if action == "booking:change_phone":
            flow = self._flows.get(state_key)
            if flow is None or flow.get("stage") != "confirm":
                return "Черновик устарел. Начните заново."
            flow["contact_method"] = None
            flow["contact_value"] = None
            flow["stage"] = "contact_value"
            await self.vk_client.send_message(
                peer_id=peer_id,
                text=PHONE_PROMPT,
                keyboard=cancel_keyboard(),
            )
            return "Напишите номер телефона."

        if action in {"booking:cancel", "flow:cancel"}:
            self._flows.pop(state_key, None)
            await self._send_home(peer_id=peer_id, user_id=user_id)
            return "Черновик заявки отменён."

        if action == "booking:submit":
            return await self._submit_preliminary_request(
                peer_id=peer_id, user_id=user_id
            )

        if action == "admin:pending":
            await self._send_pending(peer_id=peer_id, user_id=user_id)
            return (
                "Показал ожидающие заявки."
                if user_id in self.settings.admin_vk_user_ids
                else "Эта команда доступна только администратору."
            )

        if action in {"admin:confirm", "admin:decline"}:
            return await self._decide_appointment(
                payload.get("appointment_id"),
                peer_id=peer_id,
                user_id=user_id,
                decision="confirmed" if action == "admin:confirm" else "declined",
            )

        await self.vk_client.send_message(
            peer_id=peer_id,
            text="Эта кнопка устарела. Откройте меню и начните заново.",
            keyboard=main_keyboard(is_admin=user_id in self.settings.admin_vk_user_ids),
        )
        return "Кнопка устарела."

    async def _send_home(self, *, peer_id: int, user_id: int) -> None:
        text = (
            "Здравствуйте! Здесь можно оставить предварительную заявку на массаж.\n"
            "Выберите день и время, укажите контакт. Тётя обсудит с вами детали "
            "и согласует запись лично. До этого заявка не подтверждена."
            + schedule_notice(self.settings)
        )
        await self.vk_client.send_message(
            peer_id=peer_id,
            text=text,
            keyboard=main_keyboard(is_admin=user_id in self.settings.admin_vk_user_ids),
        )

    async def _show_dates(self, *, peer_id: int, offset: int) -> None:
        days = available_days(settings=self.settings, repository=self.repository)
        if not days:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Свободных слотов пока нет. Попробуйте проверить расписание позже.",
                keyboard=main_keyboard(is_admin=False),
            )
            return

        page_size = VK_DATE_PAGE_SIZE
        last_offset = ((len(days) - 1) // page_size) * page_size
        offset = min((offset // page_size) * page_size, last_offset)
        await self.vk_client.send_message(
            peer_id=peer_id,
            text="Выберите день для предварительной заявки:"
            + schedule_notice(self.settings),
            keyboard=date_keyboard(days, offset=offset),
        )

    async def _show_slots(self, raw_day: Any, *, peer_id: int) -> None:
        try:
            day = date.fromisoformat(str(raw_day))
        except (TypeError, ValueError):
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Не удалось прочитать дату. Откройте список дат заново.",
            )
            return

        if not date_is_in_booking_horizon(day, settings=self.settings):
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Эта дата уже недоступна. Выберите день из списка.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return

        slots = slots_for_day(day, settings=self.settings, repository=self.repository)
        if not slots:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="На эту дату больше нет свободного времени. Выберите другой день.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return

        await self.vk_client.send_message(
            peer_id=peer_id,
            text=f"Свободное время на {format_day(day)}. Выберите время для предварительной заявки:",
            keyboard=slot_keyboard(slots),
        )

    async def _select_slot(
        self, raw_start_ts: Any, *, peer_id: int, user_id: int
    ) -> None:
        try:
            start_ts = int(raw_start_ts)
            selected_start = datetime.fromtimestamp(start_ts, self.settings.timezone)
        except (TypeError, ValueError, OverflowError, OSError):
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Не удалось прочитать время. Выберите слот ещё раз.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return

        if not date_is_in_booking_horizon(
            selected_start.date(), settings=self.settings
        ):
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Эта дата уже недоступна. Выберите день из списка.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return

        slots = slots_for_day(
            selected_start.date(), settings=self.settings, repository=self.repository
        )
        selected = next((slot for slot in slots if slot.start_ts == start_ts), None)
        if selected is None:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Это время уже недоступно. Выберите другой слот.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return

        state_key = (peer_id, user_id)
        self._flows[state_key] = {
            "start_ts": selected.start_ts,
            "end_ts": selected.end_ts,
            "stage": "contact_value",
        }
        await self.vk_client.send_message(
            peer_id=peer_id,
            text=PHONE_PROMPT + "\nЧтобы отменить оформление, отправьте /cancel.",
            keyboard=cancel_keyboard(),
        )

    async def _receive_name(self, raw_name: str, *, peer_id: int, user_id: int) -> None:
        try:
            name = normalize_client_name(raw_name)
        except ValueError as exc:
            await self.vk_client.send_message(
                peer_id=peer_id, text=f"{exc} Или отправьте /cancel."
            )
            return

        flow = self._flows[(peer_id, user_id)]
        flow["client_name"] = name
        flow["stage"] = "confirm"
        await self._send_preliminary_preview(peer_id=peer_id, flow=flow)

    async def _select_contact_method(
        self, raw_method: Any, *, peer_id: int, user_id: int
    ) -> str:
        state_key = (peer_id, user_id)
        flow = self._flows.get(state_key)
        method = str(raw_method or "")
        if flow is None or flow.get("stage") != "contact_method":
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Черновик устарел. Выберите день и время заново.",
            )
            return "Черновик устарел."
        if method not in CONTACT_METHOD_LABELS:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Выберите способ связи одной из кнопок.",
                keyboard=contact_method_keyboard(),
            )
            return "Выберите способ из списка."

        flow["contact_method"] = method
        if "client_name" in flow:
            flow["stage"] = "confirm"
            await self._send_preliminary_preview(peer_id=peer_id, flow=flow)
            return "Проверьте сводку заявки."

        flow["stage"] = "name"
        await self.vk_client.send_message(
            peer_id=peer_id,
            text="Как к вам обращаться? Напишите имя.\nЧтобы отменить, отправьте /cancel.",
            keyboard=cancel_keyboard(),
        )
        return "Теперь укажите имя."

    async def _receive_contact_value(
        self, raw_value: str, *, peer_id: int, user_id: int
    ) -> None:
        flow = self._flows.get((peer_id, user_id))
        if flow is None or flow.get("stage") != "contact_value":
            return
        try:
            value = normalize_phone_number(raw_value)
        except ValueError as exc:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text=f"{exc}\nПопробуйте ещё раз или отправьте /cancel.",
            )
            return

        flow["contact_value"] = value
        flow["stage"] = "contact_method"
        await self.vk_client.send_message(
            peer_id=peer_id,
            text=CONTACT_METHOD_PROMPT,
            keyboard=contact_method_keyboard(),
        )

    async def _send_preliminary_preview(
        self, *, peer_id: int, flow: dict[str, Any]
    ) -> None:
        try:
            text = preliminary_summary(
                client_name=str(flow["client_name"]),
                start_ts=int(flow["start_ts"]),
                end_ts=int(flow["end_ts"]),
                contact_method=str(flow["contact_method"]),
                contact_value=str(flow["contact_value"]),
                settings=self.settings,
            )
        except (KeyError, TypeError, ValueError):
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Не хватает данных для заявки. Начните заново.",
                keyboard=main_keyboard(is_admin=False),
            )
            return
        await self.vk_client.send_message(
            peer_id=peer_id,
            text=text,
            keyboard=preliminary_confirmation_keyboard(),
        )

    async def _submit_preliminary_request(self, *, peer_id: int, user_id: int) -> str:
        state_key = (peer_id, user_id)
        flow = self._flows.get(state_key)
        if flow is None or flow.get("stage") != "confirm":
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Черновик заявки устарел. Начните оформление заново.",
            )
            return "Черновик устарел."

        try:
            client_name = normalize_client_name(str(flow["client_name"]))
            contact_method = str(flow["contact_method"])
            contact_value = normalize_contact_value(
                contact_method, str(flow["contact_value"])
            )
            start_ts = int(flow["start_ts"])
            end_ts = int(flow["end_ts"])
        except (KeyError, TypeError, ValueError):
            self._flows.pop(state_key, None)
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Не хватает данных для заявки. Начните заново.",
            )
            return "Не хватает данных."

        start_local = datetime.fromtimestamp(start_ts, self.settings.timezone)
        current_slots = slots_for_day(
            start_local.date(), settings=self.settings, repository=self.repository
        )
        if not any(
            slot.start_ts == start_ts and slot.end_ts == end_ts
            for slot in current_slots
        ):
            self._flows.pop(state_key, None)
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Это время уже недоступно. Начните заново и выберите другой слот.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return "Это время уже недоступно."

        try:
            appointment = self.repository.create_pending(
                client_name=client_name,
                contact_method=contact_method,
                contact_value=contact_value,
                service_name=self.settings.service_name,
                start_ts=start_ts,
                end_ts=end_ts,
                source_channel="vk",
                external_user_id=str(user_id),
                external_chat_id=str(peer_id),
                client_username=None,
                buffer_seconds=self.settings.buffer_minutes * 60,
            )
        except SlotUnavailableError:
            self._flows.pop(state_key, None)
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Это время только что заняли. Начните заново и выберите другой слот.",
            )
            await self._show_dates(peer_id=peer_id, offset=0)
            return "Это время только что заняли."

        self._flows.pop(state_key, None)
        await self.notifications.send_client_message(
            appointment,
            f"Предварительная заявка №{appointment.id} отправлена тёте.\n"
            "Это ещё не запись. Тётя свяжется с вами по указанному номеру "
            "и выбранным способом, чтобы лично обсудить и согласовать время.",
        )
        await self.notifications.notify_admins(appointment)
        return f"Предварительная заявка №{appointment.id} отправлена."

    async def _send_pending(self, *, peer_id: int, user_id: int) -> None:
        if user_id not in self.settings.admin_vk_user_ids:
            await self.vk_client.send_message(
                peer_id=peer_id, text="Эта команда доступна только администратору."
            )
            return

        pending = self.repository.get_pending()
        if not pending:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Заявок, ожидающих личного согласования, нет.",
            )
            return

        for appointment in pending:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text=format_appointment(appointment, self.settings),
                keyboard=admin_keyboard(appointment.id),
            )

    async def _decide_appointment(
        self,
        raw_appointment_id: Any,
        *,
        peer_id: int,
        user_id: int,
        decision: str,
    ) -> str:
        if user_id not in self.settings.admin_vk_user_ids:
            return "Эта кнопка доступна только администратору."
        try:
            appointment_id = int(raw_appointment_id)
        except (TypeError, ValueError):
            return "Некорректный номер заявки."

        appointment = self.repository.decide(
            appointment_id,
            status=decision,
            admin_id=f"vk:{user_id}",
        )
        if appointment is None:
            await self.vk_client.send_message(
                peer_id=peer_id,
                text="Заявка уже обработана или не найдена.",
            )
            return "Заявка уже обработана или не найдена."

        label = "согласовали" if decision == "confirmed" else "не согласовали"
        await self.vk_client.send_message(
            peer_id=peer_id,
            text=f"Заявка №{appointment.id}: с клиентом {label}.",
        )
        await self.notifications.notify_client_decision(appointment)
        return f"Заявка №{appointment.id}: {label}."
