from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

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
from .notifications import (
    NotificationService,
    format_appointment,
    telegram_admin_keyboard,
)
from .schedule import Slot, format_day, format_slot

logger = logging.getLogger(__name__)


class BookingFlow(StatesGroup):
    waiting_for_contact_value = State()
    waiting_for_contact_method = State()
    waiting_for_name = State()
    confirming_preliminary_request = State()


def _main_keyboard(*, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📅 Оставить заявку", callback_data="book:choose")]
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📥 Заявки для согласования", callback_data="admin:pending"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _date_keyboard(days: list[tuple[date, list[Slot]]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(days), 2):
        row = [
            InlineKeyboardButton(
                text=format_day(day),
                callback_data=f"book:date:{day.isoformat()}",
            )
            for day, _ in days[index : index + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="↩️ В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _slot_keyboard(slots: list[Slot]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(slots), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=format_slot(slot),
                    callback_data=f"book:slot:{slot.start_ts}",
                )
                for slot in slots[index : index + 2]
            ]
        )
    rows.append([InlineKeyboardButton(text="↩️ К датам", callback_data="book:choose")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="booking:cancel")]
        ]
    )


def _contact_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ВКонтакте", callback_data="contact:vk")],
            [InlineKeyboardButton(text="MAX", callback_data="contact:max")],
            [
                InlineKeyboardButton(
                    text="📞 Телефонный звонок", callback_data="contact:phone"
                )
            ],
        ]
    )


def _preliminary_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить предварительную заявку",
                    callback_data="booking:submit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить способ связи",
                    callback_data="booking:change_contact",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📞 Изменить номер телефона",
                    callback_data="booking:change_phone",
                )
            ],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="booking:cancel")],
        ]
    )


async def _edit_or_send(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if callback.message is None:
        await callback.answer("Откройте бота в личном чате.", show_alert=True)
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


def create_dispatcher(
    settings: Settings,
    repository: AppointmentRepository,
    notifications: NotificationService | None = None,
) -> Dispatcher:
    router = Router(name="booking")
    dispatcher = Dispatcher(storage=MemoryStorage())
    notification_service = notifications or NotificationService(settings)

    def is_admin(user_id: int | None) -> bool:
        return user_id is not None and user_id in settings.admin_telegram_ids

    async def show_dates(callback: CallbackQuery) -> None:
        days = available_days(settings=settings, repository=repository)
        if not days:
            await callback.answer("Пока нет свободного времени.", show_alert=True)
            if callback.message is not None:
                await callback.message.answer(
                    "Свободных слотов пока нет. Попробуйте проверить расписание позже."
                )
            return
        await _edit_or_send(
            callback,
            "Выберите день для предварительной заявки:" + schedule_notice(settings),
            _date_keyboard(days),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        user_id = message.from_user.id if message.from_user else None
        text = (
            "Здравствуйте! Здесь можно оставить предварительную заявку на массаж.\n"
            "Выберите день и время, а затем укажите, как с вами связаться. "
            "Тётя лично обсудит с вами детали и согласует запись. "
            "До этого заявка не является подтверждённой записью."
            + schedule_notice(settings)
        )
        await message.answer(
            text, reply_markup=_main_keyboard(is_admin=is_admin(user_id))
        )

    @router.message(Command("myid"))
    async def my_id(message: Message) -> None:
        if message.from_user is None:
            return
        await message.answer(
            f"Ваш Telegram ID: {message.from_user.id}\n"
            "Для настройки администратора добавьте этот номер в ADMIN_TELEGRAM_IDS в файле .env."
        )

    @router.message(Command("cancel"))
    async def cancel_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Черновик заявки отменён. Чтобы начать заново, нажмите /start."
        )

    @router.message(Command("pending"))
    async def pending_command(message: Message) -> None:
        if message.from_user is None or not is_admin(message.from_user.id):
            await message.answer("Эта команда доступна только администратору.")
            return
        pending = repository.get_pending()
        if not pending:
            await message.answer("Заявок, ожидающих личного согласования, нет.")
            return
        for appointment in pending:
            await message.answer(
                format_appointment(appointment, settings),
                reply_markup=telegram_admin_keyboard(appointment.id),
            )

    @router.callback_query(F.data == "menu:main")
    async def main_menu(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        user_id = callback.from_user.id if callback.from_user else None
        await _edit_or_send(
            callback,
            "Главное меню. Заявка будет предварительной до личного согласования.",
            _main_keyboard(is_admin=is_admin(user_id)),
        )

    @router.callback_query(F.data == "book:choose")
    async def choose_date(callback: CallbackQuery) -> None:
        await show_dates(callback)

    @router.callback_query(F.data.startswith("book:date:"))
    async def choose_time(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        try:
            day = date.fromisoformat(callback.data.removeprefix("book:date:"))
        except ValueError:
            await callback.answer(
                "Не удалось прочитать дату. Попробуйте ещё раз.", show_alert=True
            )
            return

        if not date_is_in_booking_horizon(day, settings=settings):
            await callback.answer(
                "Эта дата уже недоступна. Выберите день из списка.", show_alert=True
            )
            return

        slots = slots_for_day(day, settings=settings, repository=repository)
        if not slots:
            await show_dates(callback)
            return

        await _edit_or_send(
            callback,
            f"Свободное время на {format_day(day)}. Выберите время для предварительной заявки:",
            _slot_keyboard(slots),
        )

    @router.callback_query(F.data.startswith("book:slot:"))
    async def select_slot(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.data is None:
            return
        try:
            selected_start_ts = int(callback.data.removeprefix("book:slot:"))
            selected_start = datetime.fromtimestamp(
                selected_start_ts, settings.timezone
            )
        except (ValueError, OverflowError, OSError):
            await callback.answer(
                "Не удалось прочитать время. Выберите слот ещё раз.", show_alert=True
            )
            return

        if not date_is_in_booking_horizon(selected_start.date(), settings=settings):
            await callback.answer(
                "Эта дата уже недоступна. Выберите день из списка.", show_alert=True
            )
            return

        slots = slots_for_day(
            selected_start.date(), settings=settings, repository=repository
        )
        selected = next(
            (slot for slot in slots if slot.start_ts == selected_start_ts), None
        )
        if selected is None:
            await show_dates(callback)
            return

        await state.update_data(start_ts=selected.start_ts, end_ts=selected.end_ts)
        await state.set_state(BookingFlow.waiting_for_contact_value)
        if callback.message is not None:
            await callback.message.answer(
                PHONE_PROMPT + "\nЧтобы отменить оформление, отправьте /cancel.",
                reply_markup=_cancel_keyboard(),
            )
        await callback.answer()

    @router.message(BookingFlow.waiting_for_contact_value)
    async def receive_contact_value(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.chat.type != "private":
            return
        try:
            contact_value = normalize_phone_number(message.text or "")
        except ValueError as exc:
            await message.answer(f"{exc}\nПопробуйте ещё раз или отправьте /cancel.")
            return

        await state.update_data(contact_value=contact_value)
        await state.set_state(BookingFlow.waiting_for_contact_method)
        await message.answer(
            CONTACT_METHOD_PROMPT,
            reply_markup=_contact_method_keyboard(),
        )

    @router.callback_query(
        BookingFlow.waiting_for_contact_method, F.data.startswith("contact:")
    )
    async def select_contact_method(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.data is None:
            return
        method = callback.data.removeprefix("contact:")
        if method not in CONTACT_METHOD_LABELS:
            await callback.answer("Выберите способ из списка.", show_alert=True)
            return
        await state.update_data(contact_method=method)
        data = await state.get_data()
        await callback.answer()
        if "client_name" in data:
            await state.set_state(BookingFlow.confirming_preliminary_request)
            if callback.message is not None:
                await callback.message.answer(
                    preliminary_summary(
                        client_name=str(data["client_name"]),
                        start_ts=int(data["start_ts"]),
                        end_ts=int(data["end_ts"]),
                        contact_method=method,
                        contact_value=str(data["contact_value"]),
                        settings=settings,
                    ),
                    reply_markup=_preliminary_confirmation_keyboard(),
                )
        else:
            await state.set_state(BookingFlow.waiting_for_name)
            if callback.message is not None:
                await callback.message.answer("Как к вам обращаться? Напишите имя.")

    @router.message(BookingFlow.waiting_for_name)
    async def receive_name(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.chat.type != "private":
            return
        try:
            client_name = normalize_client_name(message.text or "")
        except ValueError as exc:
            await message.answer(f"{exc} Или отправьте /cancel.")
            return

        await state.update_data(client_name=client_name)
        await state.set_state(BookingFlow.confirming_preliminary_request)
        data = await state.get_data()
        await message.answer(
            preliminary_summary(
                client_name=client_name,
                start_ts=int(data["start_ts"]),
                end_ts=int(data["end_ts"]),
                contact_method=str(data["contact_method"]),
                contact_value=str(data["contact_value"]),
                settings=settings,
            ),
            reply_markup=_preliminary_confirmation_keyboard(),
        )

    @router.callback_query(
        BookingFlow.confirming_preliminary_request,
        F.data == "booking:change_contact",
    )
    async def change_contact_method(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(contact_method=None)
        await state.set_state(BookingFlow.waiting_for_contact_method)
        await _edit_or_send(
            callback,
            CONTACT_METHOD_PROMPT,
            _contact_method_keyboard(),
        )

    @router.callback_query(
        BookingFlow.confirming_preliminary_request,
        F.data == "booking:change_phone",
    )
    async def change_phone_number(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(contact_method=None, contact_value=None)
        await state.set_state(BookingFlow.waiting_for_contact_value)
        await _edit_or_send(callback, PHONE_PROMPT, _cancel_keyboard())

    @router.callback_query(F.data == "booking:cancel")
    async def cancel_preliminary_request(
        callback: CallbackQuery, state: FSMContext
    ) -> None:
        await state.clear()
        await callback.answer("Черновик заявки отменён.")
        if callback.message is not None:
            try:
                await callback.message.edit_text(
                    "Черновик предварительной заявки отменён."
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    "Черновик предварительной заявки отменён."
                )

    @router.callback_query(
        BookingFlow.confirming_preliminary_request, F.data == "booking:submit"
    )
    async def submit_preliminary_request(
        callback: CallbackQuery, state: FSMContext, bot: Bot
    ) -> None:
        if callback.from_user is None or callback.message is None:
            await callback.answer("Откройте бота в личном чате.", show_alert=True)
            return
        data = await state.get_data()
        try:
            client_name = normalize_client_name(str(data.get("client_name", "")))
            contact_method = str(data.get("contact_method", ""))
            contact_value = normalize_contact_value(
                contact_method, str(data.get("contact_value", ""))
            )
            start_ts = int(data["start_ts"])
            end_ts = int(data["end_ts"])
        except (KeyError, TypeError, ValueError) as exc:
            await state.clear()
            await callback.answer(f"Не хватает данных: {exc}", show_alert=True)
            return

        start_local = datetime.fromtimestamp(start_ts, settings.timezone)
        current_slots = slots_for_day(
            start_local.date(), settings=settings, repository=repository
        )
        if not any(
            slot.start_ts == start_ts and slot.end_ts == end_ts
            for slot in current_slots
        ):
            await state.clear()
            await callback.answer(
                "Это время уже недоступно. Начните заново и выберите другой слот.",
                show_alert=True,
            )
            return

        user = callback.from_user
        try:
            appointment = repository.create_pending(
                client_name=client_name,
                contact_method=contact_method,
                contact_value=contact_value,
                service_name=settings.service_name,
                start_ts=start_ts,
                end_ts=end_ts,
                source_channel="telegram",
                external_user_id=str(user.id),
                external_chat_id=str(callback.message.chat.id),
                client_username=None,
                buffer_seconds=settings.buffer_minutes * 60,
            )
        except SlotUnavailableError:
            await state.clear()
            await callback.answer(
                "Это время только что заняли. Начните заново и выберите другой слот.",
                show_alert=True,
            )
            return

        await state.clear()
        await callback.answer("Предварительная заявка отправлена.")
        acknowledgement = (
            f"Предварительная заявка №{appointment.id} отправлена тёте.\n"
            "Это ещё не запись. Тётя свяжется с вами по указанному номеру "
            "и выбранным способом, "
            "чтобы лично обсудить и согласовать время."
        )
        try:
            await callback.message.edit_text(acknowledgement)
        except TelegramBadRequest:
            await callback.message.answer(acknowledgement)

        if (
            not settings.admin_telegram_ids
            and not settings.admin_vk_user_ids
            and not settings.max_admin_user_ids
        ):
            logger.error(
                "Предварительная заявка #%s сохранена, но ID администратора не настроен.",
                appointment.id,
            )
            return
        await notification_service.notify_admins(appointment, telegram_bot=bot)

    @router.callback_query(F.data == "admin:pending")
    async def pending_button(callback: CallbackQuery) -> None:
        if callback.from_user is None or not is_admin(callback.from_user.id):
            await callback.answer(
                "Эта кнопка доступна только администратору.", show_alert=True
            )
            return
        pending = repository.get_pending()
        await callback.answer()
        if callback.message is None:
            return
        if not pending:
            await callback.message.answer(
                "Заявок, ожидающих личного согласования, нет."
            )
            return
        for appointment in pending:
            await callback.message.answer(
                format_appointment(appointment, settings),
                reply_markup=telegram_admin_keyboard(appointment.id),
            )

    @router.callback_query(F.data.startswith("admin:confirm:"))
    async def confirm_appointment(callback: CallbackQuery, bot: Bot) -> None:
        await decide_appointment(callback, bot, decision="confirmed")

    @router.callback_query(F.data.startswith("admin:decline:"))
    async def decline_appointment(callback: CallbackQuery, bot: Bot) -> None:
        await decide_appointment(callback, bot, decision="declined")

    async def decide_appointment(
        callback: CallbackQuery, bot: Bot, *, decision: str
    ) -> None:
        if callback.from_user is None or not is_admin(callback.from_user.id):
            await callback.answer(
                "Эта кнопка доступна только администратору.", show_alert=True
            )
            return
        if callback.data is None:
            return
        try:
            appointment_id = int(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Некорректный номер заявки.", show_alert=True)
            return

        appointment = repository.decide(
            appointment_id,
            status=decision,
            admin_id=f"telegram:{callback.from_user.id}",
        )
        if appointment is None:
            await callback.answer(
                "Заявка уже обработана или не найдена.", show_alert=True
            )
            return

        await callback.answer("Решение сохранено.")
        if callback.message is not None:
            try:
                await callback.message.edit_text(
                    format_appointment(appointment, settings), reply_markup=None
                )
            except TelegramBadRequest:
                await callback.message.answer(format_appointment(appointment, settings))

        await notification_service.notify_client_decision(appointment, telegram_bot=bot)

    dispatcher.include_router(router)
    return dispatcher
